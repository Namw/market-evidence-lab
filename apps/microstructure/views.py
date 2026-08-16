from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .calculations import floor_time
from .models import (
    MicrostructureCollectorRun,
    OrderBookFiveMinuteSummary,
    OrderBookSnapshot,
)
from .process_control import CollectorControlError, launch_collector, stop_collector

RECENT_SUMMARY_LIMIT = 10


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _run_payload(run: MicrostructureCollectorRun | None) -> dict[str, object]:
    if run is None:
        return {
            "id": None,
            "status": "stopped",
            "status_label": "未启动",
            "connection_state": "disconnected",
            "connection_label": "未连接",
            "received_messages": 0,
            "saved_snapshots": 0,
            "reconnect_count": 0,
            "heartbeat_at": None,
            "started_at": None,
            "stopped_at": None,
            "error_message": "",
        }
    return {
        "id": run.pk,
        "status": run.status,
        "status_label": run.get_status_display(),
        "connection_state": run.connection_state,
        "connection_label": run.get_connection_state_display(),
        "received_messages": run.received_messages,
        "saved_snapshots": run.saved_snapshots,
        "reconnect_count": run.reconnect_count,
        "heartbeat_at": _utc_iso(run.heartbeat_at),
        "started_at": _utc_iso(run.started_at),
        "stopped_at": _utc_iso(run.stopped_at),
        "error_message": run.error_message,
    }


def _snapshot_payload(snapshot: OrderBookSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "sampled_at": _utc_iso(snapshot.sampled_at),
        "event_time": _utc_iso(snapshot.event_time),
        "best_bid": _decimal(snapshot.best_bid),
        "best_ask": _decimal(snapshot.best_ask),
        "mid_price": _decimal(snapshot.mid_price),
        "spread_bps": _decimal(snapshot.spread_bps),
        "bid_depth_top20_quote": _decimal(snapshot.bid_depth_top20_quote),
        "ask_depth_top20_quote": _decimal(snapshot.ask_depth_top20_quote),
        "imbalance_top20": _decimal(snapshot.imbalance_top20),
    }


def _summary_payload(summary: OrderBookFiveMinuteSummary) -> dict[str, object]:
    return {
        "interval_start": _utc_iso(summary.interval_start),
        "mid_open": _decimal(summary.mid_open),
        "mid_high": _decimal(summary.mid_high),
        "mid_low": _decimal(summary.mid_low),
        "mid_close": _decimal(summary.mid_close),
        "spread_bps_mean": _decimal(summary.spread_bps_mean),
        "bid_depth_top20_quote_mean": _decimal(
            summary.bid_depth_top20_quote_mean
        ),
        "ask_depth_top20_quote_mean": _decimal(
            summary.ask_depth_top20_quote_mean
        ),
        "imbalance_top20_mean": _decimal(summary.imbalance_top20_mean),
        "snapshot_count": summary.snapshot_count,
    }


def _status_payload() -> dict[str, object]:
    symbol = settings.MICROSTRUCTURE_SYMBOL
    now = timezone.now().astimezone(UTC)
    interval_start = floor_time(now, seconds=300)
    active_statuses = {
        MicrostructureCollectorRun.Status.STARTING,
        MicrostructureCollectorRun.Status.RUNNING,
        MicrostructureCollectorRun.Status.STOPPING,
    }
    latest_run = (
        MicrostructureCollectorRun.objects.filter(status__in=active_statuses)
        .order_by("-created_at")
        .first()
        or MicrostructureCollectorRun.objects.order_by("-created_at").first()
    )
    latest_snapshot = (
        OrderBookSnapshot.objects.filter(symbol=symbol)
        .order_by("-sampled_at")
        .first()
    )
    summaries = list(
        OrderBookFiveMinuteSummary.objects.filter(symbol=symbol)
        .order_by("-interval_start")[:RECENT_SUMMARY_LIMIT]
    )
    current_snapshot_count = OrderBookSnapshot.objects.filter(
        symbol=symbol,
        sampled_at__gte=interval_start,
        sampled_at__lt=interval_start + timedelta(minutes=5),
    ).count()
    elapsed_seconds = int((now - interval_start).total_seconds())
    active = latest_run and latest_run.status in active_statuses
    stoppable = latest_run and latest_run.status in {
        MicrostructureCollectorRun.Status.STARTING,
        MicrostructureCollectorRun.Status.RUNNING,
    }
    return {
        "symbol": symbol,
        "server_time": _utc_iso(now),
        "run": _run_payload(latest_run),
        "can_start": not active,
        "can_stop": bool(stoppable),
        "current_interval_start": _utc_iso(interval_start),
        "current_interval_elapsed_seconds": elapsed_seconds,
        "current_interval_progress": min(elapsed_seconds / 300 * 100, 100),
        "current_snapshot_count": current_snapshot_count,
        "total_snapshot_count": OrderBookSnapshot.objects.filter(symbol=symbol).count(),
        "total_summary_count": OrderBookFiveMinuteSummary.objects.filter(
            symbol=symbol
        ).count(),
        "latest_snapshot": _snapshot_payload(latest_snapshot),
        "recent_summaries": [_summary_payload(item) for item in summaries],
    }


@require_GET
def index(request):
    return render(
        request,
        "microstructure/index.html",
        {
            "symbol": settings.MICROSTRUCTURE_SYMBOL,
            "initial_status": _status_payload(),
        },
    )


@require_GET
def status(request):
    return JsonResponse(_status_payload())


@require_POST
def start(request):
    try:
        run = launch_collector(symbol=settings.MICROSTRUCTURE_SYMBOL)
    except CollectorControlError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=409)
    return JsonResponse(
        {"ok": True, "message": "盘口采集正在启动。", "run_id": run.pk},
        status=202,
    )


@require_POST
def stop(request):
    try:
        run = stop_collector()
    except CollectorControlError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=409)
    return JsonResponse(
        {"ok": True, "message": "已发送停止信号。", "run_id": run.pk},
        status=202,
    )
