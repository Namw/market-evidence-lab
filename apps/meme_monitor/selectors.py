from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import F, Max
from django.utils import timezone

from apps.meme_monitor.models import (
    MemeAnomalyEventRecord,
    MemeContinuationResearchEpisode,
    MemeMonitorCycle,
    MemeMonitorRun,
    MemeMonitorSchedule,
    MemePairState,
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
    latest_snapshot_at = MemePairState.objects.aggregate(value=Max("observed_at"))[
        "value"
    ]
    recent_cycles = list(MemeMonitorCycle.objects.select_related("run")[:20])
    latest_cycle = recent_cycles[0] if recent_cycles else None
    candidate_pair_count = (
        MemePairState.objects.filter(pair_created_at__gte=pair_cutoff)
        .values("pair_address")
        .distinct()
        .count()
    )

    return {
        **_page_context(
            active_page="overview",
            title="Meme 新币观察",
            description=(
                "只保留当前 Pair 状态、异常证据和研究结果；这里展示监控健康度、"
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
        "snapshot_count": MemePairState.objects.count(),
        "snapshots_last_hour": MemePairState.objects.filter(
            observed_at__gte=observed_at - timedelta(hours=1)
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
    records = MemeAnomalyEventRecord.objects.select_related("continuation_episode").order_by(
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
                "最新报警优先；显示触发证据与迁移校正后的可执行 5 分钟研究结果。"
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
                "查看最近创建且正在跟踪的 BSC Pool；每个 Pair 只展示当前状态。"
            ),
        ),
        "latest_pairs": list(page.object_list),
        "page": page,
        "candidate_pair_count": len(latest_pairs),
        "new_pair_max_age_hours": settings.MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS,
    }


def research_context(*, page_number: str | int | None = None) -> dict:
    episodes = MemeContinuationResearchEpisode.objects.select_related(
        "trigger_event"
    ).all()
    page = Paginator(episodes, 30).get_page(page_number)
    completed = episodes.filter(
        status=MemeContinuationResearchEpisode.Status.COMPLETED,
        net_return_pct__isnull=False,
    )
    net_returns = list(completed.values_list("net_return_pct", flat=True))
    positive_count = sum(value > 0 for value in net_returns)
    return {
        **_page_context(
            active_page="research",
            title="首次异常 5 分钟延续性研究",
            description=(
                "只记录 BSC launchpad Token 的首次异常；按可执行入场价建样本，"
                "并在池迁移后切换到目标池完成 5 分钟观察。"
            ),
        ),
        "episodes": list(page.object_list),
        "page": page,
        "episode_count": episodes.count(),
        "tracking_count": episodes.filter(
            status__in=(
                MemeContinuationResearchEpisode.Status.WAITING_ENTRY,
                MemeContinuationResearchEpisode.Status.WAITING_EXIT,
            )
        ).count(),
        "completed_count": len(net_returns),
        "unavailable_count": episodes.filter(
            status=MemeContinuationResearchEpisode.Status.UNAVAILABLE
        ).count(),
        "migrated_count": episodes.exclude(
            migrated_destination_pair_address=""
        ).count(),
        "positive_rate": (
            Decimal(positive_count) / Decimal(len(net_returns)) * Decimal(100)
            if net_returns
            else None
        ),
        "median_net_return": median(net_returns) if net_returns else None,
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
        MemePairState.objects.filter(pair_created_at__gte=pair_cutoff)
        .order_by("-observed_at")
    )
    records.sort(
            key=lambda item: (item.pair_created_at, item.observed_at),
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
            "outcomes": [_episode_outcome(event, now=now)],
        }
        for event in records
    ]


def _episode_outcome(
    event: MemeAnomalyEventRecord,
    *,
    now: datetime,
) -> dict:
    episode = getattr(event, "continuation_episode", None)
    if episode is None:
        return {
            "minutes": 5,
            "status": "unavailable",
            "return_pct": None,
            "snapshot_at": None,
        }
    if episode.status == MemeContinuationResearchEpisode.Status.COMPLETED:
        return {
            "minutes": 5,
            "status": "observed",
            "return_pct": episode.net_return_pct,
            "snapshot_at": episode.exit_observed_at,
            "price_usd": episode.exit_price_usd,
            "css_class": (
                "is-positive" if episode.net_return_pct is not None and episode.net_return_pct >= 0 else "is-negative"
            ),
        }
    return {
        "minutes": 5,
        "status": "pending" if episode.status != MemeContinuationResearchEpisode.Status.UNAVAILABLE else "unavailable",
        "return_pct": None,
        "snapshot_at": None,
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
