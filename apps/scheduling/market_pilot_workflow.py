from __future__ import annotations

from datetime import time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.microstructure.market_monitor import monitor_market_windows
from apps.microstructure.market_pilot import analyze_microstructure_with_deepseek
from apps.microstructure.models import MarketPilotRun

from .models import MarketPilotSchedule, SCHEDULE_TIMEZONE
from .services import calculate_next_interval_run_at


MARKET_PILOT_SCHEDULE_NAME = "ETH 四小时 AI 异常影子监控"
MARKET_PILOT_DEFAULT_RUN_TIME = time(0, 10)
MARKET_PILOT_INTERVAL_HOURS = 4
ZEC_MARKET_PILOT_SCHEDULE_NAME = "ZEC 两小时 AI 微观结构异常监控"
ZEC_MARKET_PILOT_DEFAULT_RUN_TIME = time(0, 20)
ZEC_MARKET_PILOT_INTERVAL_HOURS = 2


def get_builtin_market_pilot_schedule() -> MarketPilotSchedule:
    schedule, _ = MarketPilotSchedule.objects.get_or_create(
        name=MARKET_PILOT_SCHEDULE_NAME,
        defaults={
            "symbol": "ETHUSDT",
            "enabled": False,
            "run_time": MARKET_PILOT_DEFAULT_RUN_TIME,
            "interval_hours": MARKET_PILOT_INTERVAL_HOURS,
            "window_hours": MARKET_PILOT_INTERVAL_HOURS,
            "include_contextual_evidence": True,
            "outcome_horizons": [4, 12, 24],
            "timezone": SCHEDULE_TIMEZONE,
            "threshold_pct": Decimal("2"),
            "next_run_at": calculate_next_interval_run_at(
                MARKET_PILOT_DEFAULT_RUN_TIME,
                interval_hours=MARKET_PILOT_INTERVAL_HOURS,
            ),
        },
    )
    structural_updates = {}
    if schedule.symbol != "ETHUSDT":
        structural_updates["symbol"] = "ETHUSDT"
    if schedule.window_hours != MARKET_PILOT_INTERVAL_HOURS:
        structural_updates["window_hours"] = MARKET_PILOT_INTERVAL_HOURS
    if not schedule.include_contextual_evidence:
        structural_updates["include_contextual_evidence"] = True
    if not schedule.outcome_horizons:
        structural_updates["outcome_horizons"] = [4, 12, 24]
    if structural_updates:
        MarketPilotSchedule.objects.filter(pk=schedule.pk).update(**structural_updates)
        schedule.refresh_from_db()
    return schedule


def get_builtin_zec_market_pilot_schedule() -> MarketPilotSchedule:
    schedule, _ = MarketPilotSchedule.objects.get_or_create(
        name=ZEC_MARKET_PILOT_SCHEDULE_NAME,
        defaults={
            "symbol": "ZECUSDT",
            "enabled": True,
            "run_time": ZEC_MARKET_PILOT_DEFAULT_RUN_TIME,
            "interval_hours": ZEC_MARKET_PILOT_INTERVAL_HOURS,
            "window_hours": ZEC_MARKET_PILOT_INTERVAL_HOURS,
            "include_contextual_evidence": False,
            "outcome_horizons": [2, 6, 12, 24],
            "timezone": SCHEDULE_TIMEZONE,
            "threshold_pct": Decimal("2.5"),
            "next_run_at": calculate_next_interval_run_at(
                ZEC_MARKET_PILOT_DEFAULT_RUN_TIME,
                interval_hours=ZEC_MARKET_PILOT_INTERVAL_HOURS,
            ),
        },
    )
    return schedule


def get_builtin_market_pilot_schedules() -> list[MarketPilotSchedule]:
    return [
        get_builtin_market_pilot_schedule(),
        get_builtin_zec_market_pilot_schedule(),
    ]


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
    outcome_horizons = tuple(
        int(item)
        for item in schedule.outcome_horizons
        if isinstance(item, int) and not isinstance(item, bool) and item > 0
    )
    if not outcome_horizons:
        outcome_horizons = (
            (4, 12, 24) if schedule.include_contextual_evidence else (2, 6, 12, 24)
        )
    kwargs = {}
    if not schedule.include_contextual_evidence:
        kwargs["analyzer"] = analyze_microstructure_with_deepseek
    return monitor_market_windows(
        symbol=schedule.symbol,
        threshold_pct=schedule.threshold_pct,
        window_hours=schedule.window_hours,
        baseline_hours=24,
        outcome_horizons=outcome_horizons,
        include_contextual_evidence=schedule.include_contextual_evidence,
        trigger=MarketPilotRun.Trigger.SCHEDULED,
        **kwargs,
    )
