from __future__ import annotations

from datetime import time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.microstructure.market_monitor import monitor_market_windows
from apps.microstructure.models import MarketPilotRun

from .models import MarketPilotSchedule, SCHEDULE_TIMEZONE
from .services import calculate_next_interval_run_at


MARKET_PILOT_SCHEDULE_NAME = "ETH 四小时 AI 异常影子监控"
MARKET_PILOT_DEFAULT_RUN_TIME = time(0, 10)
MARKET_PILOT_INTERVAL_HOURS = 4


def get_builtin_market_pilot_schedule() -> MarketPilotSchedule:
    schedule, _ = MarketPilotSchedule.objects.get_or_create(
        name=MARKET_PILOT_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "run_time": MARKET_PILOT_DEFAULT_RUN_TIME,
            "interval_hours": MARKET_PILOT_INTERVAL_HOURS,
            "timezone": SCHEDULE_TIMEZONE,
            "threshold_pct": Decimal("2"),
            "next_run_at": calculate_next_interval_run_at(
                MARKET_PILOT_DEFAULT_RUN_TIME,
                interval_hours=MARKET_PILOT_INTERVAL_HOURS,
            ),
        },
    )
    return schedule


@transaction.atomic
def claim_due_market_pilot_schedules(*, now=None) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    if MarketPilotRun.objects.select_for_update().filter(
        mode=MarketPilotRun.Mode.LIVE,
        status=MarketPilotRun.Status.RUNNING,
    ).exists():
        return []
    claimed = []
    due = (
        MarketPilotSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in due:
        schedule.last_run_at = claimed_at
        schedule.next_run_at = calculate_next_interval_run_at(
            schedule.run_time,
            interval_hours=schedule.interval_hours,
            after=claimed_at,
        )
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed.append(schedule.pk)
    return claimed


def execute_claimed_market_pilot_workflow(schedule_id: int) -> MarketPilotRun:
    schedule = MarketPilotSchedule.objects.get(pk=schedule_id)
    return monitor_market_windows(
        symbol="ETHUSDT",
        threshold_pct=schedule.threshold_pct,
        trigger=MarketPilotRun.Trigger.SCHEDULED,
    )
