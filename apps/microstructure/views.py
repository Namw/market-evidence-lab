from __future__ import annotations

import math
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
    multiplier: Decimal,
) -> str:
    if lower is None and upper is None:
        return "待计算"
    def label(value: Decimal) -> str:
        return f"{value * multiplier:.{places}f}{suffix}"

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


def _shared_return_max(results: list[dict[str, object]]) -> Decimal:
    return_values = [
        abs(summary["mean_future_return"] * Decimal(100))
        for result in results
        for group in result["groups"]
        for summary in (group["discovery"], group["validation"])
        if summary["mean_future_return"] is not None
    ]
    maximum = max(return_values, default=Decimal("0.01")) * Decimal("1.15")
    return max(maximum, Decimal("0.01"))


def _prepare_chart_data(
    result: dict[str, object],
    *,
    return_max: Decimal | None = None,
) -> None:
    groups = result["groups"]
    if return_max is None:
        return_max = _shared_return_max([result])
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


def _format_research_result(result: dict[str, object]) -> None:
    for group in result["groups"]:
        group["range_label"] = _metric_range(
            group["lower"],
            group["upper"],
            places=result["metric"]["range_places"],
            suffix=result["metric"]["range_suffix"],
            multiplier=result["metric"]["range_multiplier"],
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
            summary["mean_return_label"] = _percent(mean_return, places=4)
            summary["up_ratio_label"] = _percent(
                summary["up_ratio"], places=1
            )


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_delta = [value - left_mean for value in left]
    right_delta = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_delta)
        * sum(value * value for value in right_delta)
    )
    if denominator == 0:
        return None
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left_delta, right_delta)
    ) / denominator


def _decorate_research_verdict(result: dict[str, object]) -> None:
    paired_groups = [
        (
            float(index),
            float(group["discovery"]["mean_future_return"]),
            float(group["validation"]["mean_future_return"]),
        )
        for index, group in enumerate(result["groups"], start=1)
        if group["discovery"]["mean_future_return"] is not None
        and group["validation"]["mean_future_return"] is not None
    ]
    indices = [item[0] for item in paired_groups]
    discovery = [item[1] for item in paired_groups]
    validation = [item[2] for item in paired_groups]
    shape_agreement = _correlation(discovery, validation)
    discovery_trend = _correlation(indices, discovery)
    validation_trend = _correlation(indices, validation)
    range_start = result["range_start"]
    range_end = result["range_end"]
    duration_days = (
        max(0.0, (range_end - range_start).total_seconds() / 86_400)
        if range_start is not None and range_end is not None
        else 0.0
    )
    sample_count = int(result["sample_count"])
    enough_groups = len(paired_groups) >= 8
    trends_agree = bool(
        discovery_trend is not None
        and validation_trend is not None
        and discovery_trend * validation_trend > 0
        and abs(discovery_trend) >= 0.45
        and abs(validation_trend) >= 0.45
    )
    stable_shape = bool(
        shape_agreement is not None
        and shape_agreement >= 0.60
        and trends_agree
    )

    if shape_agreement is None:
        agreement_label = "不可计算"
    elif shape_agreement >= 0.60:
        agreement_label = "高"
    elif shape_agreement >= 0.30:
        agreement_label = "中"
    else:
        agreement_label = "低"

    if sample_count < 1_000 or not enough_groups:
        level = "insufficient"
        label = "数据不足"
        headline = "样本不够，暂时不能判断"
        if sample_count == 0:
            detail = "还没有同时具备指标值和未来5分钟结果的样本。"
        else:
            detail = (
                f"目前只有 {sample_count:,} 条可用样本，且十分位分组尚不完整；"
                "继续采集后再判断。"
            )
    elif stable_shape and sample_count >= 10_000 and duration_days >= 7:
        level = "candidate"
        label = "候选信号"
        headline = "前后两段数据呈现一致趋势"
        detail = (
            "发现集与验证集走势方向一致，可进入阈值和稳健性验证；"
            "这仍不是交易或告警结论。"
        )
    elif stable_shape:
        level = "watch"
        label = "继续观察"
        headline = "出现初步一致走势，但证据还不够"
        detail = (
            "前后两段数据开始呈现同向趋势，但样本量或覆盖周期仍短，"
            "暂不设置异常阈值。"
        )
    else:
        level = "rejected"
        label = "未通过验证"
        headline = "历史规律没有在后段数据中稳定复现"
        detail = (
            "橙色验证结果没有稳定复现蓝色发现结果，当前不应把这个指标"
            "用于异常告警。"
        )

    result["verdict"] = {
        "level": level,
        "label": label,
        "headline": headline,
        "detail": detail,
        "agreement_label": agreement_label,
        "paired_group_count": len(paired_groups),
    }


def _research_duration_label(result: dict[str, object]) -> str:
    range_start = result["range_start"]
    range_end = result["range_end"]
    if range_start is None or range_end is None:
        return "等待数据"
    hours = max(0.0, (range_end - range_start).total_seconds() / 3_600)
    if hours < 72:
        return f"{hours:.1f} 小时"
    return f"{hours / 24:.1f} 天"


def _research_overview(results: list[dict[str, object]]) -> dict[str, object]:
    counts = {
        level: sum(
            result["verdict"]["level"] == level for result in results
        )
        for level in ("candidate", "watch", "rejected", "insufficient")
    }
    candidate_count = counts["candidate"]
    if candidate_count:
        label = "发现候选"
        headline = f"{candidate_count} 个指标出现可继续验证的关系"
        detail = (
            "它们通过了当前页面的初步一致性检查，但仍需阈值、成本和更多"
            "市场阶段验证。"
        )
        level = "candidate"
    else:
        label = "暂无可用信号"
        headline = "目前没有指标通过样本外验证"
        detail = (
            "这意味着页面现在只能用于积累证据，不能据此触发异常告警或"
            "交易判断。"
        )
        level = "neutral"
    first = results[0] if results else None
    return {
        "symbol": settings.MICROSTRUCTURE_SYMBOL,
        "minute_count": first["minute_count"] if first else 0,
        "labeled_count": first["labeled_count"] if first else 0,
        "range_start": first["range_start"] if first else None,
        "range_end": first["range_end"] if first else None,
        "duration_label": _research_duration_label(first) if first else "等待数据",
        "counts": counts,
        "level": level,
        "label": label,
        "headline": headline,
        "detail": detail,
    }


@require_GET
def research(request):
    results = [
        build_decile_research(
            settings.MICROSTRUCTURE_SYMBOL,
            metric_key=metric_key,
        )
        for metric_key in RESEARCH_METRICS
    ]
    shared_return_max = _shared_return_max(results)
    for result in results:
        _decorate_research_verdict(result)
        _format_research_result(result)
        _prepare_chart_data(result, return_max=shared_return_max)
    overview = _research_overview(results)
    return render(
        request,
        "microstructure/research.html",
        {
            "overview": overview,
            "research_items": results,
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
