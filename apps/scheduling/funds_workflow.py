from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.market_funds.collectors import (
    collect_address_balances,
    collect_etf_flows,
    collect_stablecoin_supply,
)
from apps.market_funds.inspection import inspect_fund_data
from apps.market_funds.models import FundDataInspectionRun

from .models import FundDataSchedule, FundDataWorkflowRun


DEFAULT_SCHEDULES = {
    FundDataSchedule.TaskType.STABLECOIN: {
        "name": "Ethereum 稳定币供应",
        "run_time": time(6, 0),
        "supplement_run_time": None,
        "lookback_days": 7,
    },
    FundDataSchedule.TaskType.ETF: {
        "name": "ETH ETF 每日资金流",
        "run_time": time(6, 0),
        "supplement_run_time": time(12, 0),
        "lookback_days": 14,
    },
    FundDataSchedule.TaskType.ADDRESSES: {
        "name": "Ethereum 公开地址余额快照",
        "run_time": time(0, 10),
        "supplement_run_time": None,
        "lookback_days": 1,
    },
}


class FundWorkflowAlreadyRunning(RuntimeError):
    pass


def calculate_next_fund_run(schedule, *, after=None):
    current = (after or timezone.now()).astimezone(UTC)
    candidates = [
        datetime.combine(current.date(), schedule.run_time, tzinfo=UTC),
    ]
    if schedule.supplement_run_time:
        candidates.append(
            datetime.combine(current.date(), schedule.supplement_run_time, tzinfo=UTC)
        )
    future = sorted(item for item in candidates if item > current)
    if future:
        return future[0]
    return datetime.combine(
        current.date() + timedelta(days=1), schedule.run_time, tzinfo=UTC
    )


def get_builtin_fund_schedules():
    schedules = []
    for task_type, defaults in DEFAULT_SCHEDULES.items():
        provisional = FundDataSchedule(
            task_type=task_type,
            run_time=defaults["run_time"],
            supplement_run_time=defaults["supplement_run_time"],
        )
        schedule, _ = FundDataSchedule.objects.get_or_create(
            task_type=task_type,
            defaults={
                **defaults,
                "enabled": False,
                "timezone": "UTC",
                "next_run_at": calculate_next_fund_run(provisional),
            },
        )
        schedules.append(schedule)
    return schedules


def _collector(task_type):
    return {
        FundDataSchedule.TaskType.STABLECOIN: collect_stablecoin_supply,
        FundDataSchedule.TaskType.ETF: collect_etf_flows,
        FundDataSchedule.TaskType.ADDRESSES: collect_address_balances,
    }[task_type]


def _execute(run, *, heartbeat_callback=None):
    if heartbeat_callback:
        heartbeat_callback()
    collection = _collector(run.task_type)(trigger=CollectionRun.Trigger.SCHEDULED if run.trigger == FundDataWorkflowRun.Trigger.SCHEDULED else CollectionRun.Trigger.MANUAL)
    inspection = inspect_fund_data(run.task_type, collection)
    run.collection_run = collection
    run.inspection_run = inspection
    run.received_count = collection.received_count
    run.inserted_count = collection.inserted_count
    run.updated_count = collection.updated_count
    run.skipped_count = collection.skipped_count
    run.failed_count = collection.failed_count
    run.quality_status = inspection.quality_status
    run.safe_error_summary = collection.error_message[:500]
    if collection.status == CollectionRun.Status.FAILED:
        run.status = (
            FundDataWorkflowRun.Status.PARTIAL
            if inspection.quality_status == FundDataInspectionRun.QualityStatus.BLOCKED
            else FundDataWorkflowRun.Status.FAILED
        )
    elif inspection.status == FundDataInspectionRun.Status.FAILED:
        run.status = FundDataWorkflowRun.Status.PARTIAL
    else:
        run.status = FundDataWorkflowRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save()
    return run


@transaction.atomic
def claim_due_fund_schedules(*, now=None):
    current = now or timezone.now()
    claimed = []
    schedules = (
        FundDataSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=current)
        .order_by("next_run_at")
    )
    for schedule in schedules:
        try:
            run = FundDataWorkflowRun.objects.create(
                schedule=schedule,
                task_type=schedule.task_type,
                trigger=FundDataWorkflowRun.Trigger.SCHEDULED,
                started_at=current,
            )
        except IntegrityError:
            continue
        schedule.last_run_at = current
        schedule.next_run_at = calculate_next_fund_run(schedule, after=current)
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed.append(run.pk)
    return claimed


def execute_claimed_fund_workflow(run_id, *, heartbeat_callback=None):
    run = FundDataWorkflowRun.objects.get(pk=run_id)
    return _execute(run, heartbeat_callback=heartbeat_callback)


def execute_manual_fund_workflow(task_type):
    try:
        with transaction.atomic():
            run = FundDataWorkflowRun.objects.create(
                task_type=task_type,
                trigger=FundDataWorkflowRun.Trigger.MANUAL,
                started_at=timezone.now(),
            )
    except IntegrityError as exc:
        raise FundWorkflowAlreadyRunning from exc
    return _execute(run)
