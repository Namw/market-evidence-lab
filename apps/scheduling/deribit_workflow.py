from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Callable

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.collection.deribit import (
    collect_deribit_dvol,
    collect_deribit_option_instruments,
    collect_deribit_option_snapshot,
    floor_to_five_minutes,
)
from apps.collection.models import CollectionRun

from .models import (
    DeribitOptionsSchedule,
    DeribitOptionsWorkflowRun,
    empty_deribit_options_details,
)
from .services import calculate_next_run_at


BUILT_IN_DERIBIT_OPTIONS_SCHEDULE_NAME = "Deribit ETH期权数据采集"
DEFAULT_RUN_TIME = time(8, 20)
DEFAULT_DVOL_LOOKBACK_DAYS = 3


class DeribitOptionsAlreadyRunning(RuntimeError):
    pass


def get_builtin_deribit_options_schedule() -> DeribitOptionsSchedule:
    schedule, _ = DeribitOptionsSchedule.objects.get_or_create(
        name=BUILT_IN_DERIBIT_OPTIONS_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "run_time": DEFAULT_RUN_TIME,
            "dvol_lookback_days": DEFAULT_DVOL_LOOKBACK_DAYS,
            "next_run_at": calculate_next_run_at(DEFAULT_RUN_TIME),
        },
    )
    return schedule


def _create_workflow_run(
    *, schedule: DeribitOptionsSchedule, claimed_at: datetime
) -> DeribitOptionsWorkflowRun:
    return DeribitOptionsWorkflowRun.objects.create(
        schedule=schedule,
        trigger=DeribitOptionsWorkflowRun.Trigger.SCHEDULED,
        observed_at=floor_to_five_minutes(claimed_at),
        dvol_lookback_days=schedule.dvol_lookback_days,
        status=DeribitOptionsWorkflowRun.Status.RUNNING,
        details=empty_deribit_options_details(),
        started_at=timezone.now(),
    )


@transaction.atomic
def claim_due_deribit_options_schedules(
    *, now: datetime | None = None
) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    claimed_ids = []
    schedules = (
        DeribitOptionsSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in schedules:
        run = _create_workflow_run(schedule=schedule, claimed_at=claimed_at)
        schedule.last_run_at = claimed_at
        schedule.next_run_at = calculate_next_run_at(
            schedule.run_time,
            after=claimed_at,
        )
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed_ids.append(run.pk)
    return claimed_ids


def _record_step(details: dict, key: str, run: CollectionRun) -> None:
    details[f"{key}_run_id"] = run.pk
    details["steps"][key] = {
        "status": run.status,
        "error_summary": (
            "" if run.status == CollectionRun.Status.SUCCESS else f"{key} collection failed"
        ),
    }


def execute_deribit_options_workflow(
    run: DeribitOptionsWorkflowRun,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> DeribitOptionsWorkflowRun:
    if run.status != DeribitOptionsWorkflowRun.Status.RUNNING:
        return run
    details = empty_deribit_options_details()
    statuses = []

    def beat() -> None:
        if heartbeat_callback is not None:
            try:
                heartbeat_callback()
            except Exception:
                pass

    collection_trigger = (
        CollectionRun.Trigger.MANUAL
        if run.trigger == DeribitOptionsWorkflowRun.Trigger.MANUAL
        else CollectionRun.Trigger.SCHEDULED
    )

    dvol_end = run.observed_at.replace(minute=0, second=0, microsecond=0)
    dvol_start = dvol_end - timedelta(days=run.dvol_lookback_days)
    beat()
    dvol_run = collect_deribit_dvol(
        dvol_start,
        dvol_end,
        trigger=collection_trigger,
    )
    _record_step(details, "dvol", dvol_run)
    statuses.append(dvol_run.status)

    beat()
    instrument_run = collect_deribit_option_instruments(
        trigger=collection_trigger,
        observed_at=run.observed_at,
    )
    _record_step(details, "instrument", instrument_run)
    statuses.append(instrument_run.status)

    if instrument_run.status == CollectionRun.Status.SUCCESS:
        beat()
        snapshot_run = collect_deribit_option_snapshot(
            trigger=collection_trigger,
            observed_at=run.observed_at,
        )
        _record_step(details, "snapshot", snapshot_run)
        statuses.append(snapshot_run.status)
    else:
        details["steps"]["snapshot"] = {
            "status": "not_run",
            "error_summary": "instrument synchronization failed",
        }
        statuses.append("not_run")

    if all(status == CollectionRun.Status.SUCCESS for status in statuses):
        run.status = DeribitOptionsWorkflowRun.Status.SUCCESS
    elif any(
        status in {CollectionRun.Status.SUCCESS, CollectionRun.Status.PARTIAL}
        for status in statuses
    ):
        run.status = DeribitOptionsWorkflowRun.Status.PARTIAL
    else:
        run.status = DeribitOptionsWorkflowRun.Status.FAILED
    errors = [
        item["error_summary"]
        for item in details["steps"].values()
        if item["error_summary"]
    ]
    run.details = details
    run.safe_error_summary = "; ".join(errors)[:500]
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "status",
            "details",
            "safe_error_summary",
            "finished_at",
        ]
    )
    beat()
    return run


def execute_claimed_deribit_options_workflow(
    workflow_run_id: int,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> DeribitOptionsWorkflowRun:
    run = DeribitOptionsWorkflowRun.objects.select_related("schedule").get(
        pk=workflow_run_id
    )
    return execute_deribit_options_workflow(
        run,
        heartbeat_callback=heartbeat_callback,
    )


def execute_manual_deribit_options_workflow(
    *,
    dvol_lookback_days: int = DEFAULT_DVOL_LOOKBACK_DAYS,
) -> DeribitOptionsWorkflowRun:
    if not 1 <= dvol_lookback_days <= 30:
        raise ValueError("dvol_lookback_days must be between 1 and 30")
    observed_at = floor_to_five_minutes(timezone.now())
    try:
        with transaction.atomic():
            run = DeribitOptionsWorkflowRun.objects.create(
                schedule=None,
                trigger=DeribitOptionsWorkflowRun.Trigger.MANUAL,
                observed_at=observed_at,
                dvol_lookback_days=dvol_lookback_days,
                status=DeribitOptionsWorkflowRun.Status.RUNNING,
                details=empty_deribit_options_details(),
                started_at=timezone.now(),
            )
    except IntegrityError as exc:
        raise DeribitOptionsAlreadyRunning from exc
    return execute_deribit_options_workflow(run)
