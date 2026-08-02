from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.derivatives_evidence.services import (
    calculate_funding_interval,
    calculate_oi_snapshot,
)
from apps.price_evidence.models import PriceEvidence
from apps.research_cases.presentation import SIGNAL_LABELS

from .models import FundingRate, Kline, OpenInterest


HUNDRED = Decimal("100")

OI_DIRECTION_LABELS = {
    "expansion": "扩张",
    "contraction": "收缩",
    "neutral": "变化不明显",
    None: "暂不判断",
}
VALUE_DIRECTION_LABELS = {
    "up": "上升",
    "down": "下降",
    "flat": "持平",
    None: "暂不判断",
}
FUNDING_DIRECTION_LABELS = {
    "significant_positive": "明显正 Funding",
    "significant_negative": "明显负 Funding",
    "positive": "正 Funding",
    "negative": "负 Funding",
    "near_zero": "接近零",
    None: "暂不判断",
}
TREND_LABELS = {"up": "上升", "down": "下降", "flat": "持平", None: "暂不判断"}
HIGH_LOW_LABELS = {
    "high_before_low": "先高后低",
    "low_before_high": "先低后高",
    "same_hour": "高低点位于同一小时",
}


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - protects display from malformed snapshots
        return None


def _number(value, places: int = 2, *, signed: bool = False) -> str:
    number = _decimal(value)
    if number is None:
        return "—"
    prefix = "+" if signed and number > 0 else ""
    formatted = f"{number:,.{places}f}".rstrip("0").rstrip(".")
    return f"{prefix}{formatted}"


def _compact(value) -> str:
    number = _decimal(value)
    if number is None:
        return "—"
    for divisor, suffix in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if abs(number) >= divisor:
            return f"{number / divisor:.2f}".rstrip("0").rstrip(".") + suffix
    return _number(number)


def _hour(value) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return "—"
    return parsed.astimezone(UTC).strftime("%H:00")


def _card(kind: str, label: str, status: str, tone: str, text: str, meta: str) -> dict[str, str]:
    return {
        "kind": kind,
        "label": label,
        "status": status,
        "tone": tone,
        "text": text,
        "meta": meta,
    }


def _daily_conclusion(selected_daily: Kline, research_case) -> dict[str, str]:
    change = (
        (selected_daily.close - selected_daily.open) / selected_daily.open * HUNDRED
        if selected_daily.open
        else Decimal("0")
    )
    amplitude = (
        (selected_daily.high - selected_daily.low) / selected_daily.open * HUNDRED
        if selected_daily.open
        else Decimal("0")
    )
    direction = "上涨" if change > 0 else "下跌" if change < 0 else "平盘"
    if research_case is not None:
        signal_labels = [
            SIGNAL_LABELS.get(signal.get("type"), signal.get("type", "未知异常"))
            for signal in research_case.anomaly_signals_snapshot
        ]
        signals = "、".join(signal_labels) or "市场异常"
        return _card(
            "daily",
            "日 K 结论",
            "已沉淀",
            "settled",
            f"当日{direction} {_number(change, signed=True)}%，振幅 {_number(amplitude)}%，触发{signals}。"
            f"收盘 {_number(selected_daily.close)}，成交量 {_compact(selected_daily.volume)}。",
            f"研究案例 #{research_case.pk} · 异常规则结论",
        )
    return _card(
        "daily",
        "日 K 结论",
        "行情事实",
        "live",
        f"当日{direction} {_number(change, signed=True)}%，振幅 {_number(amplitude)}%。"
        f"收盘 {_number(selected_daily.close)}，成交量 {_compact(selected_daily.volume)}。",
        "该日期未建立异常研究案例，展示当前日 K 事实",
    )


def _hourly_facts(rows: list[Kline]) -> str:
    if not rows:
        return "当前日期没有可用的小时 K 数据。"
    safe_rows = [row for row in rows if row.open]
    if not safe_rows:
        return f"已采集 {len(rows)} 根小时 K，但没有可安全计算的开盘价。"
    highest = max(rows, key=lambda row: row.high)
    lowest = min(rows, key=lambda row: row.low)
    order = "先高后低" if highest.open_time < lowest.open_time else "先低后高"
    if highest.open_time == lowest.open_time:
        order = "高低点位于同一小时"
    changes = [((row.close - row.open) / row.open * HUNDRED, row) for row in safe_rows]
    largest_change, largest = max(changes, key=lambda item: abs(item[0]))
    up_count = sum(row.close > row.open for row in rows)
    down_count = sum(row.close < row.open for row in rows)
    return (
        f"共 {len(rows)} 根小时 K，{order}；上涨 {up_count} 小时、下跌 {down_count} 小时。"
        f"最大单小时涨跌出现在 {_hour(largest.open_time)}，幅度 {_number(largest_change, signed=True)}%。"
    )


def _hourly_conclusion(rows: list[Kline], price_evidence) -> dict[str, str]:
    if price_evidence is not None:
        if price_evidence.quality_status == PriceEvidence.QualityStatus.COMPLETE:
            metrics = price_evidence.metrics_snapshot
            high_low = metrics.get("high_low", {})
            counts = metrics.get("hour_counts", {})
            largest = metrics.get("largest_hourly_change", {})
            order = HIGH_LOW_LABELS.get(high_low.get("order"), "日内路径已完成计算")
            text = (
                f"日内{order}；上涨 {counts.get('up', 0)} 小时、下跌 {counts.get('down', 0)} 小时。"
                f"最大单小时涨跌出现在 {_hour(largest.get('open_time'))}，"
                f"幅度 {_number(largest.get('change_pct'), signed=True)}%。"
            )
            return _card(
                "hourly",
                "小时 K 结论",
                "已沉淀",
                "settled",
                text,
                f"{price_evidence.actual_count}/{price_evidence.expected_count} 小时覆盖 · 价格证据 {price_evidence.calculation_version}",
            )
        return _card(
            "hourly",
            "小时 K 结论",
            price_evidence.get_quality_status_display(),
            "missing",
            _hourly_facts(rows),
            "价格证据质量门槛未通过，仅展示已有数据事实",
        )
    return _card(
        "hourly",
        "小时 K 结论",
        "实时概括" if rows else "暂无数据",
        "live" if rows else "missing",
        _hourly_facts(rows),
        "该日期尚未沉淀价格证据",
    )


def _oi_text(snapshot: dict) -> str:
    if snapshot.get("status") != "complete":
        reasons = snapshot.get("status_reasons") or ["OI 边界数据不完整，暂不输出方向性结论。"]
        return " ".join(reasons)
    start = (snapshot.get("start") or {}).get("sum_open_interest")
    end = (snapshot.get("end") or {}).get("sum_open_interest")
    direction = OI_DIRECTION_LABELS.get(snapshot.get("quantity_direction"), "暂不判断")
    value_direction = VALUE_DIRECTION_LABELS.get(snapshot.get("value_direction"), "暂不判断")
    return (
        f"OI 从 {_compact(start)} 变至 {_compact(end)}，净变化 {_compact(snapshot.get('quantity_change'))} "
        f"（{_number(snapshot.get('quantity_change_pct'), signed=True)}%），结论为{direction}。"
        f"名义价值方向为{value_direction}。"
    )


def _oi_conclusion(rows: list[OpenInterest], derivatives_evidence, day_start: datetime) -> dict[str, str]:
    settled_snapshot = None
    if derivatives_evidence is not None and derivatives_evidence.status != "failed":
        settled_snapshot = derivatives_evidence.calculation_snapshot.get("oi")
    snapshot = settled_snapshot or calculate_oi_snapshot(rows, day_start)
    complete = snapshot.get("status") == "complete"
    settled = bool(settled_snapshot)
    return _card(
        "oi",
        "OI 结论",
        "已沉淀" if settled else "实时概括" if complete else "数据不足",
        "settled" if settled else "live" if complete else "missing",
        _oi_text(snapshot),
        f"{snapshot.get('actual_count', len(rows))}/{snapshot.get('expected_count', 25)} 个小时边界"
        + (" · 衍生品证据" if settled else " · 当前数据规则计算"),
    )


def _funding_text(snapshot: dict) -> str:
    if snapshot.get("status") != "complete":
        return snapshot.get("status_reason") or "Funding 结算数据不完整，暂不输出方向性结论。"
    average = _decimal(snapshot.get("average"))
    net_change = _decimal(snapshot.get("net_change"))
    average_pct = average * HUNDRED if average is not None else None
    net_change_pct = net_change * HUNDRED if net_change is not None else None
    direction = FUNDING_DIRECTION_LABELS.get(snapshot.get("average_direction"), "暂不判断")
    trend = TREND_LABELS.get(snapshot.get("trend"), "暂不判断")
    return (
        f"当日 {snapshot.get('actual_count', 0)} 个实际结算点，平均 Funding "
        f"{_number(average_pct, 4, signed=True)}%，整体为{direction}。"
        f"日内净变化 {_number(net_change_pct, 4, signed=True)}%，趋势{trend}。"
    )


def _funding_conclusion(
    rows: list[FundingRate], derivatives_evidence, day_start: datetime
) -> dict[str, str]:
    settled_snapshot = None
    if derivatives_evidence is not None and derivatives_evidence.status != "failed":
        settled_snapshot = next(
            (
                interval
                for interval in derivatives_evidence.calculation_snapshot.get("funding_intervals", [])
                if interval.get("label") == "event_day"
            ),
            None,
        )
    snapshot = settled_snapshot or calculate_funding_interval(
        "event_day",
        day_start,
        day_start + timedelta(days=1),
        rows,
    )
    complete = snapshot.get("status") == "complete"
    settled = bool(settled_snapshot)
    return _card(
        "funding",
        "Funding 结论",
        "已沉淀" if settled else "实时概括" if complete else "数据不足",
        "settled" if settled else "live" if complete else "missing",
        _funding_text(snapshot),
        f"{snapshot.get('actual_count', len(rows))}/{snapshot.get('expected_count', 3)} 个实际结算点"
        + (" · 衍生品证据" if settled else " · 当前数据规则计算"),
    )


def build_data_conclusions(
    *,
    selected_daily: Kline,
    selected_hourly_rows: list[Kline],
    selected_oi_rows: list[OpenInterest],
    selected_funding_rows: list[FundingRate],
    research_case=None,
) -> list[dict[str, str]]:
    day_start = selected_daily.open_time.astimezone(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    price_evidence = getattr(research_case, "price_evidence", None) if research_case else None
    derivatives_evidence = (
        getattr(research_case, "derivatives_evidence", None) if research_case else None
    )
    return [
        _daily_conclusion(selected_daily, research_case),
        _hourly_conclusion(selected_hourly_rows, price_evidence),
        _oi_conclusion(selected_oi_rows, derivatives_evidence, day_start),
        _funding_conclusion(selected_funding_rows, derivatives_evidence, day_start),
    ]
