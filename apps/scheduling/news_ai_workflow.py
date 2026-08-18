from __future__ import annotations

from datetime import time
from typing import Callable

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.news_analysis.event_merge import (
    EventMergeAlreadyRunning,
    estimate_event_merge_work,
    event_merge_inputs_changed,
    run_event_merge,
)
from apps.news_analysis.models import (
    EventMergeRun,
    NewsAnalysisRun,
    ObjectiveFactExtractionRun,
)
from apps.news_analysis.objective_facts import (
    ObjectiveFactAlreadyRunning,
    run_objective_fact_extraction,
)
from apps.news_analysis.services import (
    AnalysisAlreadyRunning,
    AnalysisExecutionFailed,
    run_news_analysis,
)

from .models import NewsAISchedule, NewsAIWorkflowRun, SCHEDULE_TIMEZONE
from .services import calculate_next_run_at


NEWS_AI_SCHEDULE_NAME = "新闻 DeepSeek 每日增量分析"
NEWS_AI_DEFAULT_RUN_TIME = time(3, 30)


class NewsAIWorkflowAlreadyRunning(RuntimeError):
    pass


def get_builtin_news_ai_schedule() -> NewsAISchedule:
    schedule, _ = NewsAISchedule.objects.get_or_create(
        name=NEWS_AI_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "run_time": NEWS_AI_DEFAULT_RUN_TIME,
            "timezone": SCHEDULE_TIMEZONE,
            "max_direction_requests": 50,
            "max_objective_records": 50,
            "max_event_ai_calls": 100,
            "next_run_at": calculate_next_run_at(NEWS_AI_DEFAULT_RUN_TIME),
        },
    )
    return schedule


def _create_run(
    *,
    trigger: str,
    schedule: NewsAISchedule | None,
    started_at=None,
) -> NewsAIWorkflowRun:
    limits = schedule or get_builtin_news_ai_schedule()
    try:
        with transaction.atomic():
            return NewsAIWorkflowRun.objects.create(
                schedule=schedule,
                trigger=trigger,
                max_direction_requests=limits.max_direction_requests,
                max_objective_records=limits.max_objective_records,
                max_event_ai_calls=limits.max_event_ai_calls,
                started_at=started_at or timezone.now(),
            )
    except IntegrityError as exc:
        if NewsAIWorkflowRun.objects.filter(
            status=NewsAIWorkflowRun.Status.RUNNING
        ).exists():
            raise NewsAIWorkflowAlreadyRunning(
                "已有新闻 AI 工作流正在运行。"
            ) from exc
        raise


def _analysis_status(status: str) -> str:
    return {
        NewsAnalysisRun.Status.SUCCESS: NewsAIWorkflowRun.StepStatus.SUCCESS,
        NewsAnalysisRun.Status.PARTIAL: NewsAIWorkflowRun.StepStatus.PARTIAL,
        NewsAnalysisRun.Status.FAILED: NewsAIWorkflowRun.StepStatus.FAILED,
        NewsAnalysisRun.Status.NOT_RUN: NewsAIWorkflowRun.StepStatus.NOT_RUN,
        NewsAnalysisRun.Status.RUNNING: NewsAIWorkflowRun.StepStatus.PENDING,
    }[status]


def _objective_status(status: str) -> str:
    return {
        ObjectiveFactExtractionRun.Status.SUCCESS: NewsAIWorkflowRun.StepStatus.SUCCESS,
        ObjectiveFactExtractionRun.Status.PARTIAL: NewsAIWorkflowRun.StepStatus.PARTIAL,
        ObjectiveFactExtractionRun.Status.FAILED: NewsAIWorkflowRun.StepStatus.FAILED,
        ObjectiveFactExtractionRun.Status.NOT_RUN: NewsAIWorkflowRun.StepStatus.NOT_RUN,
        ObjectiveFactExtractionRun.Status.RUNNING: NewsAIWorkflowRun.StepStatus.PENDING,
    }[status]


def _event_status(status: str) -> str:
    return {
        EventMergeRun.Status.SUCCEEDED: NewsAIWorkflowRun.StepStatus.SUCCESS,
        EventMergeRun.Status.SUCCEEDED_WITH_WARNINGS: NewsAIWorkflowRun.StepStatus.PARTIAL,
        EventMergeRun.Status.FAILED: NewsAIWorkflowRun.StepStatus.FAILED,
        EventMergeRun.Status.CANCELLED: NewsAIWorkflowRun.StepStatus.NOT_RUN,
        EventMergeRun.Status.PENDING: NewsAIWorkflowRun.StepStatus.PENDING,
        EventMergeRun.Status.RUNNING: NewsAIWorkflowRun.StepStatus.PENDING,
    }[status]


def _sync_usage(run: NewsAIWorkflowRun) -> None:
    analysis = run.analysis_run
    objective = run.objective_fact_run
    event = run.event_merge_run
    run.request_count = sum(
        (
            analysis.api_request_count if analysis else 0,
            objective.request_count if objective else 0,
            event.ai_decision_count if event else 0,
        )
    )
    run.input_tokens = sum(
        (
            analysis.input_tokens if analysis else 0,
            objective.prompt_tokens if objective else 0,
            event.prompt_tokens if event else 0,
        )
    )
    run.output_tokens = sum(
        (
            analysis.output_tokens if analysis else 0,
            objective.completion_tokens if objective else 0,
            event.completion_tokens if event else 0,
        )
    )
    run.total_tokens = run.input_tokens + run.output_tokens


def execute_news_ai_workflow(
    *,
    trigger: str = NewsAIWorkflowRun.Trigger.MANUAL,
    schedule: NewsAISchedule | None = None,
    workflow_run: NewsAIWorkflowRun | None = None,
    analysis_client=None,
    objective_fact_client=None,
    event_merge_client=None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> NewsAIWorkflowRun:
    if workflow_run is None:
        workflow_run = _create_run(trigger=trigger, schedule=schedule)
    elif workflow_run.status != NewsAIWorkflowRun.Status.RUNNING:
        return workflow_run

    safe_errors: list[str] = []

    def beat() -> None:
        if heartbeat_callback is not None:
            try:
                heartbeat_callback()
            except Exception:
                pass

    analysis_trigger = (
        NewsAnalysisRun.Trigger.SCHEDULED
        if workflow_run.trigger == NewsAIWorkflowRun.Trigger.SCHEDULED
        else NewsAnalysisRun.Trigger.MANUAL
    )
    try:
        beat()
        try:
            analysis = run_news_analysis(
                mode=NewsAnalysisRun.Mode.INCREMENTAL,
                trigger=analysis_trigger,
                client=analysis_client,
                max_requests=workflow_run.max_direction_requests,
            )
        except AnalysisExecutionFailed as exc:
            analysis = exc.run
        except AnalysisAlreadyRunning:
            analysis = None
            safe_errors.append("新闻方向分析正在运行，本轮未重复启动。")
        workflow_run.analysis_run = analysis
        workflow_run.analysis_status = (
            _analysis_status(analysis.status)
            if analysis is not None
            else NewsAIWorkflowRun.StepStatus.NOT_RUN
        )
        if analysis is not None and analysis.safe_error_summary:
            safe_errors.append(analysis.safe_error_summary)
        workflow_run.save(
            update_fields=["analysis_run", "analysis_status", "updated_at"]
        )

        beat()
        try:
            objective = run_objective_fact_extraction(
                mode=ObjectiveFactExtractionRun.Mode.INCREMENTAL,
                trigger=ObjectiveFactExtractionRun.Trigger.COMMAND,
                triggered_by=f"news_ai_workflow:{workflow_run.id}",
                client=objective_fact_client,
                max_records=workflow_run.max_objective_records,
            )
        except ObjectiveFactAlreadyRunning:
            objective = None
            safe_errors.append("客观事实提取正在运行，本轮未重复启动。")
        workflow_run.objective_fact_run = objective
        workflow_run.objective_fact_status = (
            _objective_status(objective.status)
            if objective is not None
            else NewsAIWorkflowRun.StepStatus.NOT_RUN
        )
        if objective is not None and objective.safe_error_summary:
            safe_errors.append(objective.safe_error_summary)
        workflow_run.save(
            update_fields=[
                "objective_fact_run",
                "objective_fact_status",
                "updated_at",
            ]
        )

        beat()
        merge = None
        workflow_run.event_merge_status = NewsAIWorkflowRun.StepStatus.NOT_RUN
        if workflow_run.objective_fact_status in {
            NewsAIWorkflowRun.StepStatus.SUCCESS,
            NewsAIWorkflowRun.StepStatus.PARTIAL,
        } and event_merge_inputs_changed():
            estimate = estimate_event_merge_work()
            estimated_calls = estimate["estimated_ai_calls"]
            if estimated_calls > workflow_run.max_event_ai_calls:
                safe_errors.append(
                    "事件合并预计需要 "
                    f"{estimated_calls} 次 AI 比较，超过本轮上限 "
                    f"{workflow_run.max_event_ai_calls}，已保留待处理。"
                )
            else:
                try:
                    merge = run_event_merge(
                        trigger=EventMergeRun.Trigger.SCHEDULED,
                        client=event_merge_client,
                    )
                except EventMergeAlreadyRunning:
                    safe_errors.append("事件合并正在运行，本轮未重复启动。")
                if merge is not None:
                    workflow_run.event_merge_status = _event_status(merge.status)
                    if merge.safe_error_summary:
                        safe_errors.append(merge.safe_error_summary)
        workflow_run.event_merge_run = merge
        workflow_run.save(
            update_fields=["event_merge_run", "event_merge_status", "updated_at"]
        )

        normal_steps = {
            NewsAIWorkflowRun.StepStatus.SUCCESS,
            NewsAIWorkflowRun.StepStatus.NOT_RUN,
        }
        statuses = {
            workflow_run.analysis_status,
            workflow_run.objective_fact_status,
            workflow_run.event_merge_status,
        }
        if statuses.issubset(normal_steps) and not safe_errors:
            workflow_run.status = NewsAIWorkflowRun.Status.SUCCESS
        elif statuses == {NewsAIWorkflowRun.StepStatus.NOT_RUN}:
            workflow_run.status = NewsAIWorkflowRun.Status.FAILED
        else:
            workflow_run.status = NewsAIWorkflowRun.Status.PARTIAL
    except Exception as exc:
        workflow_run.status = NewsAIWorkflowRun.Status.FAILED
        safe_errors.append(f"新闻 AI 工作流发生 {type(exc).__name__}。")

    _sync_usage(workflow_run)
    workflow_run.safe_error_summary = " ".join(dict.fromkeys(safe_errors))[:1000]
    workflow_run.finished_at = timezone.now()
    workflow_run.save()
    beat()
    return workflow_run


@transaction.atomic
def claim_due_news_ai_schedules(*, now=None) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    if NewsAIWorkflowRun.objects.select_for_update().filter(
        status=NewsAIWorkflowRun.Status.RUNNING
    ).exists():
        return []
    claimed = []
    due = (
        NewsAISchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in due:
        run = _create_run(
            trigger=NewsAIWorkflowRun.Trigger.SCHEDULED,
            schedule=schedule,
            started_at=claimed_at,
        )
        schedule.last_run_at = claimed_at
        schedule.next_run_at = calculate_next_run_at(
            schedule.run_time,
            after=claimed_at,
        )
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed.append(run.pk)
    return claimed


def execute_claimed_news_ai_workflow(
    run_id: int,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> NewsAIWorkflowRun:
    run = NewsAIWorkflowRun.objects.select_related("schedule").get(pk=run_id)
    return execute_news_ai_workflow(
        workflow_run=run,
        heartbeat_callback=heartbeat_callback,
    )
