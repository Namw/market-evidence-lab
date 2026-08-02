from __future__ import annotations

from datetime import datetime, time
from typing import Callable

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.inspection.models import NewsInspectionRun
from apps.inspection.news import inspect_news_collection
from apps.news_analysis.models import NewsAnalysisRun
from apps.news_analysis.services import (
    AnalysisAlreadyRunning,
    AnalysisExecutionFailed,
    run_news_analysis,
)
from apps.news_data.models import NewsFeed
from apps.news_data.sources import (
    BINANCE_ANNOUNCEMENTS_CODE,
    ETHEREUM_FOUNDATION_CODE,
    FEED_DEFINITIONS,
)
from apps.news_data.services import collect_news_feed

from .models import (
    NewsWorkflowFeedRun,
    NewsWorkflowRun,
    NewsWorkflowSchedule,
    SCHEDULE_TIMEZONE,
)
from .services import calculate_next_run_at


NEWS_WORKFLOW_SCHEDULE_NAME = "新闻每日采集、质量检查与增量分析"
NEWS_WORKFLOW_DEFAULT_RUN_TIME = time(8, 35)


class NewsWorkflowAlreadyRunning(Exception):
    pass


def get_builtin_news_schedule() -> NewsWorkflowSchedule:
    schedule, _ = NewsWorkflowSchedule.objects.get_or_create(
        name=NEWS_WORKFLOW_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "run_time": NEWS_WORKFLOW_DEFAULT_RUN_TIME,
            "timezone": SCHEDULE_TIMEZONE,
            "next_run_at": calculate_next_run_at(NEWS_WORKFLOW_DEFAULT_RUN_TIME),
        },
    )
    return schedule


def _create_news_workflow_run(
    *,
    trigger: str,
    schedule: NewsWorkflowSchedule | None,
    started_at: datetime | None = None,
) -> NewsWorkflowRun:
    try:
        with transaction.atomic():
            return NewsWorkflowRun.objects.create(
                schedule=schedule,
                trigger=trigger,
                status=NewsWorkflowRun.Status.RUNNING,
                started_at=started_at or timezone.now(),
            )
    except IntegrityError as exc:
        if NewsWorkflowRun.objects.filter(
            status=NewsWorkflowRun.Status.RUNNING
        ).exists():
            raise NewsWorkflowAlreadyRunning(
                "已有新闻每日工作流正在运行。"
            ) from exc
        raise


def _safe_exception(exc: Exception, step: str) -> str:
    return f"{step} encountered {exc.__class__.__name__}."


def _status_summary(step: str, run_id: int | None, status: str) -> str:
    identity = f" #{run_id}" if run_id else ""
    return f"{step}{identity} returned status {status}."


def _quality_issue_count(run: NewsInspectionRun) -> int:
    if run.quality_status == NewsInspectionRun.QualityStatus.PASSED:
        return 0
    return max(len(run.reasons or []), 1)


def _save_progress(run: NewsWorkflowRun, *fields: str) -> None:
    run.save(update_fields=[*fields])


LEGACY_FEED_FIELDS = {
    ETHEREUM_FOUNDATION_CODE: "ethereum",
    BINANCE_ANNOUNCEMENTS_CODE: "binance",
}


def _sync_legacy_feed_fields(
    workflow_run: NewsWorkflowRun,
    feed_code: str,
    *,
    collection_run=None,
    collection_status: str | None = None,
    inspection_run=None,
    quality_status: str | None = None,
) -> None:
    prefix = LEGACY_FEED_FIELDS.get(feed_code)
    if prefix is None:
        return
    fields = []
    for suffix, value in (
        ("collection_run", collection_run),
        ("collection_status", collection_status),
        ("inspection_run", inspection_run),
        ("quality_status", quality_status),
    ):
        if value is None:
            continue
        field = f"{prefix}_{suffix}"
        setattr(workflow_run, field, value)
        fields.append(field)
    if fields:
        _save_progress(workflow_run, *fields)


def execute_news_workflow(
    *,
    trigger: str = NewsWorkflowRun.Trigger.MANUAL,
    schedule: NewsWorkflowSchedule | None = None,
    workflow_run: NewsWorkflowRun | None = None,
    range_end: datetime | None = None,
    collection_clients: dict[str, object] | None = None,
    analysis_client=None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> NewsWorkflowRun:
    """Collect, inspect and incrementally analyze news with isolated child runs."""
    if workflow_run is None:
        workflow_run = _create_news_workflow_run(
            trigger=trigger,
            schedule=schedule,
        )
    elif workflow_run.status != NewsWorkflowRun.Status.RUNNING:
        return workflow_run

    effective_end = range_end or workflow_run.started_at
    clients = collection_clients or {}
    safe_errors: list[str] = []

    def beat() -> None:
        if heartbeat_callback is None:
            return
        try:
            heartbeat_callback()
        except Exception:
            pass

    feeds = list(
        NewsFeed.objects.filter(enabled=True, source__enabled=True)
        .select_related("source")
        .order_by("source__code", "code")
    )
    feed_order = {code: index for index, code in enumerate(FEED_DEFINITIONS)}
    feeds.sort(key=lambda feed: feed_order.get(feed.code, len(feed_order)))
    collected_runs: dict[str, CollectionRun] = {}
    feed_steps: dict[str, NewsWorkflowFeedRun] = {}
    for feed in feeds:
        beat()
        label = str(feed)
        step, _ = NewsWorkflowFeedRun.objects.get_or_create(
            workflow_run=workflow_run,
            feed=feed,
        )
        feed_steps[feed.code] = step
        try:
            collection_run = collect_news_feed(
                feed.code,
                trigger=workflow_run.trigger,
                range_end=effective_end,
                client=clients.get(feed.code) or clients.get(feed.source.code),
            )
        except Exception as exc:
            step.collection_status = NewsWorkflowRun.StepStatus.FAILED
            step.quality_status = NewsWorkflowRun.QualityStatus.NOT_RUN
            step.safe_error_summary = _safe_exception(exc, f"{label} collection")
            step.save(
                update_fields=[
                    "collection_status",
                    "quality_status",
                    "safe_error_summary",
                    "updated_at",
                ]
            )
            safe_errors.append(_safe_exception(exc, f"{label} collection"))
            _sync_legacy_feed_fields(
                workflow_run,
                feed.code,
                collection_status=NewsWorkflowRun.StepStatus.FAILED,
                quality_status=NewsWorkflowRun.QualityStatus.NOT_RUN,
            )
            continue

        collected_runs[feed.code] = collection_run
        step.collection_run = collection_run
        step.collection_status = collection_run.status
        step.save(
            update_fields=["collection_run", "collection_status", "updated_at"]
        )
        workflow_run.inserted_count += collection_run.inserted_count
        workflow_run.updated_count += collection_run.updated_count
        workflow_run.skipped_count += collection_run.skipped_count
        if collection_run.status != CollectionRun.Status.SUCCESS:
            safe_errors.append(
                _status_summary(label, collection_run.pk, collection_run.status)
            )
        _save_progress(
            workflow_run, "inserted_count", "updated_count", "skipped_count"
        )
        _sync_legacy_feed_fields(
            workflow_run,
            feed.code,
            collection_run=collection_run,
            collection_status=collection_run.status,
        )
        beat()

    for feed in feeds:
        label = str(feed)
        step = feed_steps[feed.code]
        collection_run = collected_runs.get(feed.code)
        if collection_run is None:
            continue
        beat()
        try:
            inspection_run = inspect_news_collection(collection_run)
        except Exception as exc:
            step.quality_status = NewsWorkflowRun.QualityStatus.FAILED
            step.safe_error_summary = _safe_exception(exc, f"{label} inspection")
            step.save(
                update_fields=["quality_status", "safe_error_summary", "updated_at"]
            )
            safe_errors.append(_safe_exception(exc, f"{label} inspection"))
            _sync_legacy_feed_fields(
                workflow_run,
                feed.code,
                quality_status=NewsWorkflowRun.QualityStatus.FAILED,
            )
            continue

        step.inspection_run = inspection_run
        step.quality_status = inspection_run.quality_status
        step.save(update_fields=["inspection_run", "quality_status", "updated_at"])
        workflow_run.quality_issue_count += _quality_issue_count(inspection_run)
        if inspection_run.status != NewsInspectionRun.Status.SUCCESS:
            safe_errors.append(
                _status_summary(
                    f"{label} inspection",
                    inspection_run.pk,
                    inspection_run.status,
                )
            )
        elif inspection_run.quality_status != NewsInspectionRun.QualityStatus.PASSED:
            safe_errors.append(
                _status_summary(
                    f"{label} quality",
                    inspection_run.pk,
                    inspection_run.quality_status,
                )
            )
        _save_progress(workflow_run, "quality_issue_count")
        _sync_legacy_feed_fields(
            workflow_run,
            feed.code,
            inspection_run=inspection_run,
            quality_status=inspection_run.quality_status,
        )

    beat()
    analysis_trigger = (
        NewsAnalysisRun.Trigger.SCHEDULED
        if workflow_run.trigger == NewsWorkflowRun.Trigger.SCHEDULED
        else NewsAnalysisRun.Trigger.MANUAL
    )
    analysis_run = None
    try:
        analysis_run = run_news_analysis(
            mode=NewsAnalysisRun.Mode.INCREMENTAL,
            trigger=analysis_trigger,
            client=analysis_client,
        )
    except AnalysisExecutionFailed as exc:
        analysis_run = exc.run
        safe_errors.append("News analysis encountered an internal execution error.")
    except AnalysisAlreadyRunning:
        workflow_run.analysis_status = NewsWorkflowRun.StepStatus.NOT_RUN
        safe_errors.append("News analysis was not run because the current version is busy.")
    except Exception as exc:
        workflow_run.analysis_status = NewsWorkflowRun.StepStatus.FAILED
        safe_errors.append(_safe_exception(exc, "News analysis"))

    if analysis_run is not None:
        workflow_run.analysis_run = analysis_run
        workflow_run.analysis_status = analysis_run.status
        workflow_run.analysis_candidate_count = analysis_run.candidate_count
        workflow_run.analysis_success_count = analysis_run.success_count
        workflow_run.analysis_failure_count = analysis_run.failure_count
        workflow_run.analysis_skipped_count = analysis_run.skipped_count
        if analysis_run.status != NewsAnalysisRun.Status.SUCCESS:
            safe_errors.append(
                analysis_run.safe_error_summary
                or _status_summary("News analysis", analysis_run.pk, analysis_run.status)
            )
    _save_progress(
        workflow_run,
        "analysis_run",
        "analysis_status",
        "analysis_candidate_count",
        "analysis_success_count",
        "analysis_failure_count",
        "analysis_skipped_count",
    )

    collection_statuses = [step.collection_status for step in feed_steps.values()]
    quality_statuses = [step.quality_status for step in feed_steps.values()]
    all_normal = (
        bool(collection_statuses)
        and all(
            status == NewsWorkflowRun.StepStatus.SUCCESS
            for status in collection_statuses
        )
        and all(
            status == NewsWorkflowRun.QualityStatus.PASSED
            for status in quality_statuses
        )
        and workflow_run.analysis_status == NewsWorkflowRun.StepStatus.SUCCESS
    )
    all_collections_failed = bool(collection_statuses) and all(
        status == NewsWorkflowRun.StepStatus.FAILED for status in collection_statuses
    )
    analysis_made_progress = workflow_run.analysis_success_count > 0
    if all_normal:
        workflow_run.status = NewsWorkflowRun.Status.SUCCESS
    elif all_collections_failed and not analysis_made_progress:
        workflow_run.status = NewsWorkflowRun.Status.FAILED
    else:
        workflow_run.status = NewsWorkflowRun.Status.PARTIAL

    workflow_run.safe_error_summary = " ".join(dict.fromkeys(safe_errors))[:1_000]
    workflow_run.finished_at = timezone.now()
    workflow_run.save(
        update_fields=[
            "status",
            "safe_error_summary",
            "finished_at",
        ]
    )
    beat()
    return workflow_run


@transaction.atomic
def claim_due_news_schedules(*, now: datetime | None = None) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    claimed_run_ids: list[int] = []
    due_schedules = (
        NewsWorkflowSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in due_schedules:
        claimed_this_schedule = False
        if not NewsWorkflowRun.objects.filter(
            status=NewsWorkflowRun.Status.RUNNING
        ).exists():
            run = _create_news_workflow_run(
                trigger=NewsWorkflowRun.Trigger.SCHEDULED,
                schedule=schedule,
                started_at=claimed_at,
            )
            claimed_run_ids.append(run.pk)
            schedule.last_run_at = claimed_at
            claimed_this_schedule = True
        schedule.next_run_at = calculate_next_run_at(
            schedule.run_time,
            after=claimed_at,
        )
        update_fields = ["next_run_at", "updated_at"]
        if claimed_this_schedule:
            update_fields.append("last_run_at")
        schedule.save(update_fields=update_fields)
    return claimed_run_ids


def execute_claimed_news_workflow(
    workflow_run_id: int,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> NewsWorkflowRun:
    workflow_run = NewsWorkflowRun.objects.select_related("schedule").get(
        pk=workflow_run_id
    )
    return execute_news_workflow(
        workflow_run=workflow_run,
        heartbeat_callback=heartbeat_callback,
    )
