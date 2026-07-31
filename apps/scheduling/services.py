from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.collection.services import collect_klines
from apps.inspection.models import KlineInspectionRun
from apps.inspection.services import inspect_klines

from .models import (
    SCHEDULE_TIMEZONE,
    KlineSchedule,
    SchedulerHeartbeat,
    WorkflowRun,
    empty_workflow_details,
)


BUILT_IN_SCHEDULE_NAME = "ETHUSDT每日K线采集与数据质量检查"
DEFAULT_RUN_TIME = time(8, 5)
DEFAULT_LOOKBACK_DAYS = 3
SYMBOL = "ETHUSDT"
INTERVALS = ("1d", "1h")


def calculate_utc_range(
    lookback_days: int,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    if not 1 <= lookback_days <= 30:
        raise ValueError("lookback_days must be between 1 and 30")
    current = now or timezone.now()
    if timezone.is_naive(current):
        raise ValueError("now must be timezone-aware")
    range_end = current.astimezone(UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return range_end - timedelta(days=lookback_days), range_end


def calculate_next_run_at(
    run_time: time,
    *,
    after: datetime | None = None,
) -> datetime:
    current = after or timezone.now()
    if timezone.is_naive(current):
        raise ValueError("after must be timezone-aware")
    schedule_zone = ZoneInfo(SCHEDULE_TIMEZONE)
    local_current = current.astimezone(schedule_zone)
    candidate = datetime.combine(
        local_current.date(),
        run_time.replace(tzinfo=None),
        tzinfo=schedule_zone,
    )
    if candidate <= local_current:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def get_builtin_schedule() -> KlineSchedule:
    schedule, _ = KlineSchedule.objects.get_or_create(
        name=BUILT_IN_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "run_time": DEFAULT_RUN_TIME,
            "timezone": SCHEDULE_TIMEZONE,
            "lookback_days": DEFAULT_LOOKBACK_DAYS,
            "next_run_at": calculate_next_run_at(DEFAULT_RUN_TIME),
        },
    )
    return schedule


def _safe_step_exception(exc: Exception) -> str:
    # Exception text may contain remote response bodies, URLs, credentials or tokens.
    # Workflow summaries deliberately retain only the exception type.
    return f"{exc.__class__.__name__}: step execution failed"


def _returned_error(label: str, run_id: int | None, status: str) -> str:
    identity = f" #{run_id}" if run_id is not None else ""
    return f"{label}{identity} returned status {status}."


def _create_workflow_run(
    *,
    lookback_days: int,
    trigger: str,
    schedule: KlineSchedule | None,
    now: datetime | None = None,
) -> WorkflowRun:
    range_start, range_end = calculate_utc_range(lookback_days, now=now)
    return WorkflowRun.objects.create(
        schedule=schedule,
        trigger=trigger,
        range_start=range_start,
        range_end=range_end,
        status=WorkflowRun.Status.RUNNING,
        quality_status=WorkflowRun.QualityStatus.PENDING,
        details=empty_workflow_details(),
        started_at=timezone.now(),
    )


def execute_workflow(
    *,
    lookback_days: int | None = None,
    trigger: str = WorkflowRun.Trigger.MANUAL,
    schedule: KlineSchedule | None = None,
    now: datetime | None = None,
    workflow_run: WorkflowRun | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> WorkflowRun:
    if workflow_run is None:
        if lookback_days is None:
            raise ValueError("lookback_days is required for a new workflow")
        workflow_run = _create_workflow_run(
            lookback_days=lookback_days,
            trigger=trigger,
            schedule=schedule,
            now=now,
        )
    elif workflow_run.status != WorkflowRun.Status.RUNNING:
        return workflow_run

    details = empty_workflow_details()
    step_statuses: list[str] = []
    successful_inspections: list[str] = []

    def beat() -> None:
        if heartbeat_callback is not None:
            try:
                heartbeat_callback()
            except Exception:
                # Monitoring must not turn a data workflow step into a failure.
                pass

    def run_collection(interval: str) -> None:
        key = f"collection_{interval}"
        run_id_key = f"collection_{interval}_run_id"
        beat()
        try:
            child = collect_klines(
                SYMBOL,
                interval,
                workflow_run.range_start,
                workflow_run.range_end,
                trigger=workflow_run.trigger,
            )
            child_status = child.status
            details[run_id_key] = child.pk
            error_summary = ""
            if child_status != CollectionRun.Status.SUCCESS:
                error_summary = _returned_error("CollectionRun", child.pk, child_status)
        except Exception as exc:
            child_status = "failed"
            error_summary = _safe_step_exception(exc)
        details["steps"][key] = {
            "status": child_status,
            "error_summary": error_summary,
        }
        step_statuses.append(child_status)

    def run_inspection(interval: str) -> None:
        key = f"inspection_{interval}"
        run_id_key = f"inspection_{interval}_run_id"
        beat()
        try:
            child = inspect_klines(
                SYMBOL,
                interval,
                workflow_run.range_start,
                workflow_run.range_end,
                trigger=workflow_run.trigger,
            )
            child_status = child.status
            details[run_id_key] = child.pk
            error_summary = ""
            if child_status == KlineInspectionRun.Status.SUCCESS:
                successful_inspections.append(child.quality_status)
            else:
                error_summary = _returned_error(
                    "KlineInspectionRun",
                    child.pk,
                    child_status,
                )
        except Exception as exc:
            child_status = "failed"
            error_summary = _safe_step_exception(exc)
        details["steps"][key] = {
            "status": child_status,
            "error_summary": error_summary,
        }
        step_statuses.append(child_status)

    for interval in INTERVALS:
        run_collection(interval)
        run_inspection(interval)

    all_successful = len(step_statuses) == 4 and all(
        status == "success" for status in step_statuses
    )
    made_progress = any(status in {"success", "partial"} for status in step_statuses)
    if all_successful:
        workflow_run.status = WorkflowRun.Status.SUCCESS
    elif made_progress:
        workflow_run.status = WorkflowRun.Status.PARTIAL
    else:
        workflow_run.status = WorkflowRun.Status.FAILED

    if KlineInspectionRun.QualityStatus.ISSUES in successful_inspections:
        workflow_run.quality_status = WorkflowRun.QualityStatus.ISSUES
    elif successful_inspections and all(
        status == KlineInspectionRun.QualityStatus.PASSED
        for status in successful_inspections
    ):
        workflow_run.quality_status = WorkflowRun.QualityStatus.PASSED
    else:
        workflow_run.quality_status = WorkflowRun.QualityStatus.UNKNOWN

    errors = [
        step["error_summary"]
        for step in details["steps"].values()
        if step["error_summary"]
    ]
    workflow_run.details = details
    workflow_run.error_message = " ".join(errors)[:1_000]
    workflow_run.finished_at = timezone.now()
    workflow_run.save(
        update_fields=[
            "status",
            "quality_status",
            "details",
            "error_message",
            "finished_at",
        ]
    )
    beat()
    return workflow_run


@transaction.atomic
def claim_due_schedules(*, now: datetime | None = None) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    claimed_run_ids: list[int] = []
    due_schedules = (
        KlineSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in due_schedules:
        run = _create_workflow_run(
            lookback_days=schedule.lookback_days,
            trigger=WorkflowRun.Trigger.SCHEDULED,
            schedule=schedule,
            now=claimed_at,
        )
        schedule.last_run_at = claimed_at
        schedule.next_run_at = calculate_next_run_at(
            schedule.run_time,
            after=claimed_at,
        )
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed_run_ids.append(run.pk)
    return claimed_run_ids


def execute_claimed_workflow(
    workflow_run_id: int,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> WorkflowRun:
    workflow_run = WorkflowRun.objects.select_related("schedule").get(
        pk=workflow_run_id
    )
    return execute_workflow(
        workflow_run=workflow_run,
        heartbeat_callback=heartbeat_callback,
    )


def record_heartbeat(
    executor_id: str,
    *,
    poll_interval_seconds: int,
    is_running: bool = True,
    now: datetime | None = None,
) -> SchedulerHeartbeat:
    heartbeat_at = now or timezone.now()
    heartbeat, _ = SchedulerHeartbeat.objects.update_or_create(
        executor_id=executor_id,
        defaults={
            "is_running": is_running,
            "poll_interval_seconds": poll_interval_seconds,
            "last_heartbeat_at": heartbeat_at,
        },
        create_defaults={
            "is_running": is_running,
            "poll_interval_seconds": poll_interval_seconds,
            "started_at": heartbeat_at,
            "last_heartbeat_at": heartbeat_at,
        },
    )
    return heartbeat


def scheduler_status(*, now: datetime | None = None) -> dict[str, object]:
    current = now or timezone.now()
    heartbeats = list(SchedulerHeartbeat.objects.all())
    latest = heartbeats[0] if heartbeats else None
    online = any(
        heartbeat.is_running
        and heartbeat.last_heartbeat_at
        >= current
        - timedelta(seconds=max(90, heartbeat.poll_interval_seconds * 3))
        for heartbeat in heartbeats
    )
    return {
        "online": online,
        "last_heartbeat_at": latest.last_heartbeat_at if latest else None,
    }
