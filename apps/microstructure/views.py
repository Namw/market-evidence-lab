from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .calculations import floor_time
from .models import MarketMinute, MicrostructureCollectorRun
from .process_control import CollectorControlError, launch_collector, stop_collector

DEFAULT_MINUTE_LIMIT = 120
MAX_MINUTE_LIMIT = 1_440
RECENT_RUN_LIMIT = 8


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


def _run_bounds(
    run: MicrostructureCollectorRun,
    *,
    now: datetime,
) -> tuple[datetime, datetime]:
    started_at = run.started_at or run.created_at
    ended_at = run.stopped_at or now
    return (
        floor_time(started_at, seconds=60),
        floor_time(ended_at, seconds=60) + timedelta(minutes=1),
    )


def _available_runs(
    *,
    symbol: str,
    now: datetime,
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    runs = MicrostructureCollectorRun.objects.filter(
        symbol=symbol,
        saved_minute_updates__gt=0,
    ).order_by("-created_at")[:RECENT_RUN_LIMIT]
    for run in runs:
        range_start, range_end = _run_bounds(run, now=now)
        minute_count = MarketMinute.objects.filter(
            symbol=symbol,
            minute_start__gte=range_start,
            minute_start__lt=range_end,
        ).count()
        if minute_count == 0:
            continue
        payloads.append(
            {
                "id": run.pk,
                "status": run.status,
                "status_label": run.get_status_display(),
                "started_at": _utc_iso(run.started_at or run.created_at),
                "stopped_at": _utc_iso(run.stopped_at),
                "range_start": _utc_iso(range_start),
                "range_end": _utc_iso(range_end),
                "minute_count": minute_count,
            }
        )
    return payloads


def _status_payload(
    *,
    minute_limit: int = DEFAULT_MINUTE_LIMIT,
    selected_run_id: int | None = None,
    before: datetime | None = None,
) -> dict[str, object]:
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
    available_runs = _available_runs(symbol=symbol, now=now)
    available_run_ids = {item["id"] for item in available_runs}
    selected_run = None
    if selected_run_id in available_run_ids:
        selected_run = MicrostructureCollectorRun.objects.filter(
            pk=selected_run_id,
            symbol=symbol,
        ).first()
    if selected_run is None and available_runs:
        selected_run = MicrostructureCollectorRun.objects.filter(
            pk=available_runs[0]["id"]
        ).first()

    minute_query = MarketMinute.objects.filter(symbol=symbol)
    range_start = range_end = None
    if selected_run is not None:
        range_start, range_end = _run_bounds(selected_run, now=now)
        minute_query = minute_query.filter(
            minute_start__gte=range_start,
            minute_start__lt=range_end,
        )
    page_query = minute_query
    if before is not None:
        page_query = page_query.filter(minute_start__lt=before)
    rows = list(
        reversed(
            page_query.order_by("-minute_start")[:minute_limit]
        )
    )
    oldest_loaded = rows[0].minute_start if rows else None
    has_more = bool(
        oldest_loaded is not None
        and minute_query.filter(minute_start__lt=oldest_loaded).exists()
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
        "minute_count": minute_query.count(),
        "minutes": [_minute_payload(row) for row in rows],
        "has_more": has_more,
        "oldest_loaded_stamp": _utc_iso(oldest_loaded),
        "selected_run_id": selected_run.pk if selected_run else None,
        "selected_run_active": bool(
            selected_run and selected_run.status in active_statuses
        ),
        "available_runs": available_runs,
        "range_start": _utc_iso(range_start),
        "range_end": _utc_iso(range_end),
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
    try:
        selected_run_id = int(request.GET.get("run_id", ""))
    except (TypeError, ValueError):
        selected_run_id = None
    before = None
    before_raw = request.GET.get("before", "")
    if before_raw:
        try:
            before = datetime.fromisoformat(before_raw.replace("Z", "+00:00"))
        except ValueError:
            before = None
    if before is not None:
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        before = before.astimezone(UTC)
    minute_limit = max(10, min(MAX_MINUTE_LIMIT, minute_limit))
    return JsonResponse(
        _status_payload(
            minute_limit=minute_limit,
            selected_run_id=selected_run_id,
            before=before,
        )
    )


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
