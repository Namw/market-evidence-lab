from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from apps.meme_monitor.models import MemeMonitorSchedule

BUILT_IN_MEME_SCHEDULE_NAME = "BSC Meme 新币市场异常检查"


def get_builtin_meme_schedule() -> MemeMonitorSchedule:
    now = timezone.now()
    schedule, _ = MemeMonitorSchedule.objects.get_or_create(
        name=BUILT_IN_MEME_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "interval_seconds": int(settings.MEME_MONITOR_POLL_INTERVAL_SECONDS),
            "next_run_at": now,
        },
    )
    return schedule


@transaction.atomic
def set_meme_schedule_enabled(
    enabled: bool,
    *,
    now: datetime | None = None,
) -> MemeMonitorSchedule:
    requested_at = now or timezone.now()
    schedule = get_builtin_meme_schedule()
    schedule = MemeMonitorSchedule.objects.select_for_update().get(pk=schedule.pk)
    schedule.enabled = enabled
    if enabled:
        schedule.next_run_at = requested_at
    schedule.save(update_fields=["enabled", "next_run_at", "updated_at"])
    return schedule


@transaction.atomic
def claim_due_meme_schedules(*, now: datetime | None = None) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    claimed_ids: list[int] = []
    schedules = (
        MemeMonitorSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in schedules:
        schedule.last_run_at = claimed_at
        schedule.next_run_at = claimed_at + timedelta(seconds=schedule.interval_seconds)
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed_ids.append(schedule.pk)
    return claimed_ids


def execute_claimed_meme_schedule(schedule_id: int) -> None:
    schedule = MemeMonitorSchedule.objects.get(pk=schedule_id)
    if not schedule.enabled:
        return
    call_command("run_meme_monitor", "--once")
