from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import F, Max
from django.utils import timezone

from apps.meme_monitor.models import (
    MemeAnomalyEventRecord,
    MemeMarketSnapshot,
    MemeMonitorCycle,
    MemeMonitorRun,
    MemeMonitorSchedule,
)
from apps.meme_monitor.scheduling import get_builtin_meme_schedule
from apps.scheduling.services import scheduler_status


def overview_context(*, now: datetime | None = None) -> dict:
    observed_at = now or timezone.now()
    pair_cutoff = observed_at - timedelta(
        hours=settings.MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS
    )
    latest_run = MemeMonitorRun.objects.first()
    schedule = get_builtin_meme_schedule()
    executor = scheduler_status(now=observed_at)
    latest_snapshot_at = MemeMarketSnapshot.objects.aggregate(value=Max("timestamp"))[
        "value"
    ]
    recent_cycles = list(MemeMonitorCycle.objects.select_related("run")[:20])
    latest_cycle = recent_cycles[0] if recent_cycles else None
    candidate_pair_count = (
        MemeMarketSnapshot.objects.filter(pair_created_at__gte=pair_cutoff)
        .values("pair_address")
        .distinct()
        .count()
    )

    return {
        **_page_context(
            active_page="overview",
            title="Meme 新币观察",
            description=(
                "持续保留新 Pair 行情快照和异常证据；这里展示监控健康度、"
                "最新市场事实与执行情况。"
            ),
        ),
        "status": _present_schedule_status(
            schedule,
            executor=executor,
            latest_run=latest_run,
        ),
        "latest_run_status": _present_status(latest_run, now=observed_at),
        "schedule": schedule,
        "scheduler": executor,
        "latest_run": latest_run,
        "latest_snapshot_at": latest_snapshot_at,
        "latest_snapshot_age_seconds": (
            max(0, int((observed_at - latest_snapshot_at).total_seconds()))
            if latest_snapshot_at
            else None
        ),
        "tracked_pair_count": (
            latest_cycle.tracked_pairs if latest_cycle else candidate_pair_count
        ),
        "candidate_pair_count": candidate_pair_count,
        "snapshot_count": MemeMarketSnapshot.objects.count(),
        "snapshots_last_hour": MemeMarketSnapshot.objects.filter(
            timestamp__gte=observed_at - timedelta(hours=1)
        ).count(),
        "events_last_24h": MemeAnomalyEventRecord.objects.filter(
            event_time__gte=observed_at - timedelta(hours=24)
        ).count(),
        "recent_cycles": recent_cycles,
        "new_pair_max_age_hours": settings.MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS,
    }


def anomalies_context(
    *,
    now: datetime | None = None,
    page_number: str | int | None = None,
) -> dict:
    observed_at = now or timezone.now()
    records = MemeAnomalyEventRecord.objects.select_related("snapshot").order_by(
        "-event_time",
        F("price_change_5m").desc(nulls_last=True),
        F("volume_5m").desc(nulls_last=True),
        "-created_at",
    )
    page = Paginator(records, 30).get_page(page_number)
    return {
        **_page_context(
            active_page="anomalies",
            title="异常事件与后续表现",
            description=(
                "最新报警优先；集中比较触发证据与 5 分钟、15 分钟、1 小时后续表现。"
            ),
        ),
        "events": _event_rows(records=page.object_list, now=observed_at),
        "page": page,
    }


def pairs_context(
    *,
    now: datetime | None = None,
    page_number: str | int | None = None,
) -> dict:
    observed_at = now or timezone.now()
    pair_cutoff = observed_at - timedelta(
        hours=settings.MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS
    )
    latest_pairs = _latest_pairs(pair_cutoff=pair_cutoff, now=observed_at)
    page = Paginator(latest_pairs, 30).get_page(page_number)
    return {
        **_page_context(
            active_page="pairs",
            title="最新跟踪 Pair",
            description=(
                "查看最近创建且正在跟踪的 BSC Pool；每个 Pair 只展示最新一条行情快照。"
            ),
        ),
        "latest_pairs": list(page.object_list),
        "page": page,
        "candidate_pair_count": len(latest_pairs),
        "new_pair_max_age_hours": settings.MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS,
    }


def _page_context(*, active_page: str, title: str, description: str) -> dict:
    return {
        "active_page": active_page,
        "page_title": title,
        "page_description": description,
        "geckoterminal_network": settings.MEME_MONITOR_NETWORK,
        "poll_interval_seconds": settings.MEME_MONITOR_POLL_INTERVAL_SECONDS,
    }


def _latest_pairs(*, pair_cutoff: datetime, now: datetime) -> list[dict]:
    records = list(
        MemeMarketSnapshot.objects.filter(pair_created_at__gte=pair_cutoff)
        .order_by("pair_address", "-timestamp")
        .distinct("pair_address")
    )
    records.sort(
        key=lambda item: (item.pair_created_at, item.timestamp),
        reverse=True,
    )
    return [
        {
            "snapshot": record,
            "pair_age_minutes": max(
                0,
                int((now - record.pair_created_at).total_seconds() // 60),
            ),
            "transaction_count_5m": (record.buys_5m or 0) + (record.sells_5m or 0),
            "price_change_5m_class": (
                "is-positive"
                if record.price_change_5m is not None and record.price_change_5m >= 0
                else "is-negative"
                if record.price_change_5m is not None
                else ""
            ),
        }
        for record in records
    ]


def _event_rows(
    *,
    records: Iterable[MemeAnomalyEventRecord],
    now: datetime,
) -> list[dict]:
    return [
        {
            "event": event,
            "outcomes": [
                _event_outcome(event, minutes=minutes, now=now)
                for minutes in (5, 15, 60)
            ],
        }
        for event in records
    ]


def _event_outcome(
    event: MemeAnomalyEventRecord,
    *,
    minutes: int,
    now: datetime,
) -> dict:
    target = event.event_time + timedelta(minutes=minutes)
    tolerance = timedelta(
        seconds=max(settings.MEME_MONITOR_POLL_INTERVAL_SECONDS * 3, 90)
    )
    snapshot = (
        MemeMarketSnapshot.objects.filter(
            source=event.snapshot.source,
            chain=event.chain,
            pair_address=event.pair_address,
            timestamp__gte=target,
            timestamp__lte=target + tolerance,
            price_usd__isnull=False,
        )
        .order_by("timestamp")
        .first()
    )
    if snapshot is None:
        return {
            "minutes": minutes,
            "status": "pending" if now <= target + tolerance else "unavailable",
            "return_pct": None,
            "snapshot_at": None,
        }
    if event.price_usd is None or event.price_usd <= 0:
        return {
            "minutes": minutes,
            "status": "unavailable",
            "return_pct": None,
            "snapshot_at": snapshot.timestamp,
        }
    return_pct = (snapshot.price_usd / event.price_usd - Decimal(1)) * Decimal(100)
    return {
        "minutes": minutes,
        "status": "observed",
        "return_pct": return_pct,
        "snapshot_at": snapshot.timestamp,
        "price_usd": snapshot.price_usd,
        "css_class": "is-positive" if return_pct >= 0 else "is-negative",
    }


def _present_status(run: MemeMonitorRun | None, *, now: datetime) -> dict:
    if run is None:
        return {
            "key": "never_started",
            "label": "尚未记录运行",
            "detail": "已有旧快照，但尚无 heartbeat 运行记录。",
        }
    stale_after_seconds = max(settings.MEME_MONITOR_POLL_INTERVAL_SECONDS * 3, 90)
    heartbeat_age = max(0, int((now - run.heartbeat_at).total_seconds()))
    if run.status == MemeMonitorRun.Status.RUNNING:
        if heartbeat_age <= stale_after_seconds:
            if run.latest_error:
                return {
                    "key": "degraded",
                    "label": "运行中 · 最近一轮有警告",
                    "detail": run.latest_error,
                }
            return {
                "key": "running",
                "label": "运行中",
                "detail": f"最后心跳距今 {heartbeat_age} 秒。",
            }
        return {
            "key": "stale",
            "label": "心跳已过期",
            "detail": f"数据库仍标记运行中，但已 {heartbeat_age} 秒没有心跳。",
        }
    if run.status == MemeMonitorRun.Status.FAILED:
        return {
            "key": "failed",
            "label": "运行失败",
            "detail": run.latest_error or "监听进程异常结束。",
        }
    return {
        "key": "stopped",
        "label": "已停止",
        "detail": "单轮执行已完成或监听进程已正常停止。",
    }


def _present_schedule_status(
    schedule: MemeMonitorSchedule,
    *,
    executor: dict[str, object],
    latest_run: MemeMonitorRun | None,
) -> dict:
    if not schedule.enabled:
        return {
            "key": "stopped",
            "label": "定时检查已关闭",
            "detail": "不会产生新的市场数据请求；已开始的当前轮次会执行完成。",
        }
    if not executor["online"]:
        return {
            "key": "stale",
            "label": "已启用 · 等待执行器",
            "detail": "定时计划已保存，但统一调度执行器当前离线。",
        }
    if (
        latest_run is not None
        and latest_run.status == MemeMonitorRun.Status.FAILED
        and schedule.last_run_at is not None
        and latest_run.started_at >= schedule.last_run_at
    ):
        return {
            "key": "degraded",
            "label": "定时检查已启用 · 最近一轮失败",
            "detail": latest_run.latest_error or "最近一轮市场检查执行失败。",
        }
    return {
        "key": "running",
        "label": "定时检查运行中",
        "detail": f"执行器在线，每 {schedule.interval_seconds} 秒检查一次到期任务。",
    }
