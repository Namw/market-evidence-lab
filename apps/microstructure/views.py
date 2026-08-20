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
from .research import RESEARCH_METRICS, build_decile_research

from apps.market_data.models import Kline, OpenInterest

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
            "oi_process_id": None,
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
        "oi_process_id": run.oi_process_id,
    }


def _minute_payload(row: MarketMinute) -> dict[str, object]:
    return {
        "minute_start": _utc_iso(row.minute_start),
        "minute_end": _utc_iso(row.minute_end),
        "open": _decimal(row.open_price),
        "high": _decimal(row.high_price),
        "low": _decimal(row.low_price),
        "close": _decimal(row.close_price),
        "future_5m_return": _decimal(row.future_5m_return),
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


def _oi_5m_payload(symbol: str, limit: int = 288) -> list[dict[str, object]]:
    rows = list(
        OpenInterest.objects.filter(
            exchange=Kline.Exchange.BINANCE,
            market_type=Kline.MarketType.USD_M_FUTURES,
            symbol=symbol,
            period=OpenInterest.Period.FIVE_MINUTES,
        )
        .order_by("-timestamp")[:limit]
    )
    return [
        {
            "timestamp": _utc_iso(row.timestamp),
            "value": _decimal(row.sum_open_interest),
            "value_usdt": _decimal(row.sum_open_interest_value),
        }
        for row in reversed(rows)
    ]


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
    ).order_by("-created_at", "-pk")[:RECENT_RUN_LIMIT]
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
        .order_by("-created_at", "-pk")
        .first()
        or MicrostructureCollectorRun.objects.order_by("-created_at", "-pk").first()
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
        "oi_5m": _oi_5m_payload(symbol),
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


def _percent(value: Decimal | None, *, places: int) -> str:
    if value is None:
        return "—"
    return f"{value * Decimal(100):.{places}f}%"


def _metric_range(
    lower: Decimal | None,
    upper: Decimal | None,
    *,
    places: int,
    suffix: str,
) -> str:
    if lower is None and upper is None:
        return "待计算"
    def label(value: Decimal) -> str:
        return f"{value:.{places}f}{suffix}"

    if lower is None:
        return f"≤ {label(upper)}"
    if upper is None:
        return f"> {label(lower)}"
    return f"({label(lower)}, {label(upper)}]"


def _chart_y(value: Decimal, *, maximum: Decimal) -> float:
    top = Decimal("34")
    height = Decimal("186")
    return float(top + (maximum - value) / (maximum * Decimal(2)) * height)


def _line_segments(groups, split: str, maximum: Decimal) -> list[str]:
    segments: list[list[str]] = []
    current: list[str] = []
    for group in groups:
        value = group[split]["mean_future_return"]
        if value is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(f'{group["chart_x"]:.1f},{_chart_y(value * Decimal(100), maximum=maximum):.1f}')
    if current:
        segments.append(current)
    return [" ".join(segment) for segment in segments]


def _prepare_chart_data(result: dict[str, object]) -> None:
    groups = result["groups"]
    return_values = [
        abs(summary["mean_future_return"] * Decimal(100))
        for group in groups
        for summary in (group["discovery"], group["validation"])
        if summary["mean_future_return"] is not None
    ]
    return_max = max(return_values, default=Decimal("0.01")) * Decimal("1.15")
    return_max = max(return_max, Decimal("0.01"))
    for index, group in enumerate(groups):
        group["chart_x"] = 65 + index * 69
        for split in ("discovery", "validation"):
            summary = group[split]
            mean_return = summary["mean_future_return"]
            summary["return_y"] = (
                _chart_y(mean_return * Decimal(100), maximum=return_max)
                if mean_return is not None
                else None
            )
            summary["return_marker_y"] = (
                summary["return_y"] - 5
                if summary["return_y"] is not None
                else None
            )
            up_ratio = summary["up_ratio"]
            summary["up_bar_height"] = (
                float(up_ratio * Decimal("186")) if up_ratio is not None else 0
            )
            summary["up_bar_y"] = 220 - summary["up_bar_height"]
    result["return_chart"] = {
        "maximum_label": f"+{return_max:.4f}%",
        "minimum_label": f"-{return_max:.4f}%",
        "zero_y": _chart_y(Decimal(0), maximum=return_max),
        "zero_label_y": _chart_y(Decimal(0), maximum=return_max) + 4,
        "discovery_segments": _line_segments(groups, "discovery", return_max),
        "validation_segments": _line_segments(groups, "validation", return_max),
    }


@require_GET
def research(request):
    metric_key = request.GET.get("metric", "trade_imbalance")
    if metric_key not in RESEARCH_METRICS:
        metric_key = "trade_imbalance"
    result = build_decile_research(
        settings.MICROSTRUCTURE_SYMBOL,
        metric_key=metric_key,
    )
    result["metric_options"] = [
        {
            **metric,
            "active": key == metric_key,
        }
        for key, metric in RESEARCH_METRICS.items()
    ]
    for group in result["groups"]:
        group["range_label"] = _metric_range(
            group["lower"],
            group["upper"],
            places=result["metric"]["range_places"],
            suffix=result["metric"]["range_suffix"],
        )
        for split in ("discovery", "validation"):
            summary = group[split]
            mean_return = summary["mean_future_return"]
            summary["return_class"] = (
                "positive"
                if mean_return is not None and mean_return > 0
                else "negative"
                if mean_return is not None and mean_return < 0
                else ""
            )
            summary["mean_return_label"] = _percent(
                mean_return, places=4
            )
            summary["up_ratio_label"] = _percent(summary["up_ratio"], places=1)
    _prepare_chart_data(result)
    return render(
        request,
        "microstructure/research.html",
        {"research": result},
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
