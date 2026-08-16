from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import MarketMinute, MicrostructureCollectorRun
from .process_control import CollectorControlError, launch_collector, stop_collector

DEFAULT_MINUTE_LIMIT = 120
MAX_MINUTE_LIMIT = 1_440


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
            "saved_minute_updates": 0,
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
        "saved_minute_updates": run.saved_minute_updates,
        "reconnect_count": run.reconnect_count,
        "heartbeat_at": _utc_iso(run.heartbeat_at),
        "started_at": _utc_iso(run.started_at),
        "stopped_at": _utc_iso(run.stopped_at),
        "error_message": run.error_message,
    }


def _minute_payload(row: MarketMinute) -> dict[str, object]:
    return {
        "minute_start": _utc_iso(row.minute_start),
        "minute_end": _utc_iso(row.minute_end),
        "open": _decimal(row.open_price),
        "high": _decimal(row.high_price),
        "low": _decimal(row.low_price),
        "close": _decimal(row.close_price),
        "quote_volume": _decimal(row.quote_volume),
        "taker_buy_quote": _decimal(row.taker_buy_quote),
        "taker_sell_quote": _decimal(row.taker_sell_quote),
        "delta_quote": _decimal(row.delta_quote),
        "trade_count": row.trade_count,
        "bid_depth_open": _decimal(row.bid_depth_open),
        "bid_depth_close": _decimal(row.bid_depth_close),
        "bid_depth_mean": _decimal(row.bid_depth_mean),
        "ask_depth_open": _decimal(row.ask_depth_open),
        "ask_depth_close": _decimal(row.ask_depth_close),
        "ask_depth_mean": _decimal(row.ask_depth_mean),
        "spread_bps_mean": _decimal(row.spread_bps_mean),
        "spread_bps_p95": _decimal(row.spread_bps_p95),
        "book_sample_count": row.book_sample_count,
        "coverage_ratio": _decimal(row.coverage_ratio),
        "closed": row.kline_closed,
    }


def _order_book_payload(
    run: MicrostructureCollectorRun | None,
) -> dict[str, object] | None:
    if run is None or not run.latest_bids or not run.latest_asks:
        return None
    return {
        "event_time": _utc_iso(run.latest_event_time),
        "update_id": run.latest_update_id,
        "bids": run.latest_bids,
        "asks": run.latest_asks,
    }


def _status_payload(*, minute_limit: int = DEFAULT_MINUTE_LIMIT) -> dict[str, object]:
    symbol = settings.MICROSTRUCTURE_SYMBOL
    now = timezone.now().astimezone(UTC)
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
    rows = list(
        reversed(
            MarketMinute.objects.filter(symbol=symbol).order_by("-minute_start")[
                :minute_limit
            ]
        )
    )
    active = bool(latest_run and latest_run.status in active_statuses)
    stoppable = bool(
        latest_run
        and latest_run.status
        in {
            MicrostructureCollectorRun.Status.STARTING,
            MicrostructureCollectorRun.Status.RUNNING,
        }
    )
    return {
        "symbol": symbol,
        "server_time": _utc_iso(now),
        "refresh_seconds": 60,
        "run": _run_payload(latest_run),
        "can_start": not active,
        "can_stop": stoppable,
        "minute_count": MarketMinute.objects.filter(symbol=symbol).count(),
        "minutes": [_minute_payload(row) for row in rows],
        "latest_order_book": _order_book_payload(latest_run),
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
    try:
        minute_limit = int(request.GET.get("minutes", DEFAULT_MINUTE_LIMIT))
    except (TypeError, ValueError):
        minute_limit = DEFAULT_MINUTE_LIMIT
    minute_limit = max(10, min(MAX_MINUTE_LIMIT, minute_limit))
    return JsonResponse(_status_payload(minute_limit=minute_limit))


@require_POST
def start(request):
    try:
        run = launch_collector(symbol=settings.MICROSTRUCTURE_SYMBOL)
    except CollectorControlError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=409)
    return JsonResponse(
        {"ok": True, "message": "实时分钟数据采集正在启动。", "run_id": run.pk},
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
