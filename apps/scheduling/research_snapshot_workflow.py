from __future__ import annotations

from datetime import datetime, time, timedelta
from time import perf_counter

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.microstructure.models import (
    MarketMinute,
    MicrostructureResearchSnapshot,
)
from apps.microstructure.research import (
    RESEARCH_CALCULATION_VERSION,
    refresh_future_5m_returns,
)
from apps.microstructure.views import build_research_page_payload

from .models import ResearchSnapshotSchedule, SCHEDULE_TIMEZONE
from .services import calculate_next_run_at


RESEARCH_SNAPSHOT_SCHEDULE_NAME = "微观结构每日研究快照"
RESEARCH_SNAPSHOT_RUN_TIME = time(0, 30)


def get_builtin_research_snapshot_schedule() -> ResearchSnapshotSchedule:
    schedule, _ = ResearchSnapshotSchedule.objects.get_or_create(
        name=RESEARCH_SNAPSHOT_SCHEDULE_NAME,
        defaults={
            "enabled": True,
            "run_time": RESEARCH_SNAPSHOT_RUN_TIME,
            "timezone": SCHEDULE_TIMEZONE,
            "next_run_at": calculate_next_run_at(RESEARCH_SNAPSHOT_RUN_TIME),
        },
    )
    return schedule


@transaction.atomic
def claim_due_research_snapshot_schedules(
    *, now: datetime | None = None,
) -> list[int]:
    claimed_at = now or timezone.now()
    if timezone.is_naive(claimed_at):
        raise ValueError("now must be timezone-aware")
    claimed_ids = []
    schedules = (
        ResearchSnapshotSchedule.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=claimed_at)
        .order_by("next_run_at", "pk")
    )
    for schedule in schedules:
        schedule.last_run_at = claimed_at
        schedule.next_run_at = calculate_next_run_at(
            schedule.run_time,
            after=claimed_at,
        )
        schedule.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        claimed_ids.append(schedule.pk)
    return claimed_ids


def generate_research_snapshot(symbol: str) -> MicrostructureResearchSnapshot:
    resolved = symbol.upper()
    cutoff = (
        MarketMinute.objects.filter(symbol=resolved, kline_closed=True)
        .order_by("-minute_start")
        .values_list("minute_start", flat=True)
        .first()
    )
    if cutoff is None:
        raise ValueError(f"{resolved} has no closed market minutes")

    previous = MicrostructureResearchSnapshot.objects.filter(symbol=resolved).first()
    candidate_start = (
        previous.data_cutoff - timedelta(minutes=5) if previous is not None else None
    )
    candidate_end = cutoff - timedelta(minutes=5)
    started = perf_counter()
    labels_updated = 0
    if candidate_start is None or candidate_start <= candidate_end:
        labels_updated = refresh_future_5m_returns(
            symbol=resolved,
            candidate_start=candidate_start,
            candidate_end=candidate_end,
        )
    payload = build_research_page_payload(resolved, cutoff=cutoff)
    overview = payload["overview"]
    duration_ms = max(0, round((perf_counter() - started) * 1_000))
    return MicrostructureResearchSnapshot.objects.create(
        symbol=resolved,
        data_cutoff=cutoff,
        calculation_version=RESEARCH_CALCULATION_VERSION,
        minute_count=int(overview["minute_count"]),
        labeled_count=int(overview["labeled_count"]),
        labels_updated=labels_updated,
        duration_ms=duration_ms,
        payload=payload,
    )


def execute_claimed_research_snapshot_schedule(
    schedule_id: int,
) -> list[MicrostructureResearchSnapshot]:
    schedule = ResearchSnapshotSchedule.objects.get(pk=schedule_id)
    try:
        snapshots = [
            generate_research_snapshot(symbol)
            for symbol in settings.MICROSTRUCTURE_SYMBOLS
        ]
    except Exception as exc:
        ResearchSnapshotSchedule.objects.filter(pk=schedule.pk).update(
            last_error=f"{type(exc).__name__}: research snapshot failed"[:500],
        )
        raise
    ResearchSnapshotSchedule.objects.filter(pk=schedule.pk).update(
        last_success_at=timezone.now(),
        last_error="",
    )
    return snapshots
