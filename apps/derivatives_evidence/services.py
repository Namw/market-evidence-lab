from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.market_data.models import FundingRate, Kline, OpenInterest
from apps.research_cases.models import ResearchCase

from .models import DerivativesEvidence


RULE_VERSION = "derivatives-evidence-v1"
HUNDRED = Decimal("100")
OI_THRESHOLD_PCT = Decimal("1")
PRICE_THRESHOLD_PCT = Decimal("0.5")
FUNDING_CROWDING_THRESHOLD = Decimal("0.0003")
EXPECTED_OI_POINTS = 25
EXPECTED_PRICE_HOURS = 24

JOINT_DESCRIPTIONS = {
    ("up", "expansion"): "价格上涨且 OI 扩张，说明新增杠杆仓位参与上涨。",
    ("down", "expansion"): "价格下跌且 OI 扩张，说明新增杠杆仓位参与下跌。",
    ("up", "contraction"): "价格上涨但 OI 收缩，更接近仓位退出或空头回补。",
    ("down", "contraction"): "价格下跌且 OI 收缩，更接近仓位退出或多头去杠杆。",
}


def decimal_string(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def utc_iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _change_pct(start: Decimal | None, end: Decimal | None) -> Decimal | None:
    if start in (None, Decimal("0")) or end is None:
        return None
    return (end - start) / start * HUNDRED


def _signed_direction(change: Decimal | None) -> str:
    if change is None or change == 0:
        return "flat"
    return "up" if change > 0 else "down"


def oi_direction(change_pct: Decimal | None) -> str | None:
    if change_pct is None:
        return None
    if change_pct >= OI_THRESHOLD_PCT:
        return "expansion"
    if change_pct <= -OI_THRESHOLD_PCT:
        return "contraction"
    return "neutral"


def price_direction(change_pct: Decimal | None) -> str | None:
    if change_pct is None:
        return None
    if change_pct >= PRICE_THRESHOLD_PCT:
        return "up"
    if change_pct <= -PRICE_THRESHOLD_PCT:
        return "down"
    return "neutral"


def funding_crowding(rate: Decimal) -> str:
    if rate >= FUNDING_CROWDING_THRESHOLD:
        return "significant_positive"
    if rate <= -FUNDING_CROWDING_THRESHOLD:
        return "significant_negative"
    if rate > 0:
        return "positive"
    if rate < 0:
        return "negative"
    return "near_zero"


def _oi_record(point: OpenInterest) -> dict[str, str | int]:
    return {
        "id": point.pk,
        "timestamp": utc_iso(point.timestamp),
        "sum_open_interest": decimal_string(point.sum_open_interest),
        "sum_open_interest_value": decimal_string(point.sum_open_interest_value),
    }


def _adjacent_extreme(points: list[OpenInterest], mode: str):
    selected = None
    for start, end in zip(points, points[1:]):
        if end.timestamp - start.timestamp != timedelta(hours=1):
            continue
        delta = end.sum_open_interest - start.sum_open_interest
        if (mode == "increase" and delta <= 0) or (
            mode == "decrease" and delta >= 0
        ):
            continue
        if selected is None or (
            mode == "increase" and delta > selected[0]
        ) or (
            mode == "decrease" and delta < selected[0]
        ):
            selected = (delta, start, end)
    if selected is None:
        return None
    delta, start, end = selected
    return {
        "start_time": utc_iso(start.timestamp),
        "end_time": utc_iso(end.timestamp),
        "start_oi": decimal_string(start.sum_open_interest),
        "end_oi": decimal_string(end.sum_open_interest),
        "change": decimal_string(delta),
        "change_pct": decimal_string(_change_pct(start.sum_open_interest, end.sum_open_interest)),
    }


def calculate_oi_snapshot(points: list[OpenInterest], day_start: datetime) -> dict:
    day_end = day_start + timedelta(days=1)
    expected = [day_start + timedelta(hours=index) for index in range(EXPECTED_OI_POINTS)]
    by_time = {point.timestamp: point for point in points}
    missing = [value for value in expected if value not in by_time]
    invalid = [
        point for point in points
        if point.timestamp.minute or point.timestamp.second or point.timestamp.microsecond
        or point.sum_open_interest < 0
        or point.sum_open_interest_value < 0
    ]
    start = by_time.get(day_start)
    end = by_time.get(day_end)
    intervals_valid = all(
        points[index].timestamp - points[index - 1].timestamp == timedelta(hours=1)
        for index in range(1, len(points))
    )
    complete = (
        len(points) == EXPECTED_OI_POINTS
        and not missing
        and not invalid
        and intervals_valid
        and start is not None
        and end is not None
        and start.sum_open_interest != 0
    )
    reasons = []
    if not points:
        reasons.append("Binance OI 历史接口仅提供最近约一个月，所需日期当前无来源数据。")
    if missing:
        reasons.append(f"缺少 {len(missing)} 个预期整点边界。")
    if invalid:
        reasons.append("存在非整点或无效 OI 数值。")
    if points and not intervals_valid:
        reasons.append("已有 OI 点之间存在非 1 小时间隔。")
    if start is not None and start.sum_open_interest == 0:
        reasons.append("日初 OI 为 0，无法安全计算百分比。")
    status = "complete" if complete else ("partial" if points else "unavailable")

    start_quantity = start.sum_open_interest if start else None
    end_quantity = end.sum_open_interest if end else None
    quantity_change = (
        end_quantity - start_quantity
        if start_quantity is not None and end_quantity is not None
        else None
    )
    quantity_change_pct = _change_pct(start_quantity, end_quantity)
    start_value = start.sum_open_interest_value if start else None
    end_value = end.sum_open_interest_value if end else None
    value_change = (
        end_value - start_value
        if start_value is not None and end_value is not None
        else None
    )
    quantity_sign = _signed_direction(quantity_change)
    value_sign = _signed_direction(value_change)
    divergence = (
        complete
        and quantity_change is not None
        and value_change is not None
        and quantity_sign in {"up", "down"}
        and value_sign in {"up", "down"}
        and quantity_sign != value_sign
    )
    direction = oi_direction(quantity_change_pct) if complete else None
    conclusion_direction = (
        direction if direction in {"expansion", "contraction"} and not divergence else None
    )
    highest = max(points, key=lambda point: point.sum_open_interest) if points else None
    lowest = min(points, key=lambda point: point.sum_open_interest) if points else None
    return {
        "status": status,
        "status_reasons": reasons,
        "expected_count": EXPECTED_OI_POINTS,
        "actual_count": len(points),
        "missing_timestamps": [utc_iso(value) for value in missing],
        "range_start": utc_iso(day_start),
        "range_end": utc_iso(day_end),
        "points": [_oi_record(point) for point in points],
        "start": _oi_record(start) if start else None,
        "end": _oi_record(end) if end else None,
        "quantity_change": decimal_string(quantity_change),
        "quantity_change_pct": decimal_string(quantity_change_pct),
        "quantity_direction": direction,
        "conclusion_direction": conclusion_direction,
        "highest": _oi_record(highest) if highest else None,
        "lowest": _oi_record(lowest) if lowest else None,
        "maximum_hourly_increase": _adjacent_extreme(points, "increase"),
        "maximum_hourly_decrease": _adjacent_extreme(points, "decrease"),
        "value_start": decimal_string(start_value),
        "value_end": decimal_string(end_value),
        "value_change": decimal_string(value_change),
        "value_direction": value_sign if complete and value_change is not None else None,
        "quantity_value_divergence": divergence,
    }


def calculate_price_snapshot(
    research_case: ResearchCase,
    klines: list[Kline],
    oi_by_time: dict[datetime, OpenInterest],
) -> dict:
    day_start = research_case.event_time
    expected = [day_start + timedelta(hours=index) for index in range(EXPECTED_PRICE_HOURS)]
    by_time = {kline.open_time: kline for kline in klines}
    missing = [value for value in expected if value not in by_time]
    safe = [kline for kline in klines if kline.open > 0]
    daily_change_pct = _change_pct(research_case.open, research_case.close)
    daily_direction = price_direction(daily_change_pct)
    selected = None
    selected_change_pct = None
    for kline in safe:
        change_pct = _change_pct(kline.open, kline.close)
        if selected is None or abs(change_pct) > abs(selected_change_pct):
            selected = kline
            selected_change_pct = change_pct
    largest = None
    if selected is not None:
        oi_start = oi_by_time.get(selected.open_time)
        oi_end = oi_by_time.get(selected.open_time + timedelta(hours=1))
        oi_change = (
            oi_end.sum_open_interest - oi_start.sum_open_interest
            if oi_start is not None and oi_end is not None
            else None
        )
        largest = {
            "open_time": utc_iso(selected.open_time),
            "end_time": utc_iso(selected.open_time + timedelta(hours=1)),
            "open": decimal_string(selected.open),
            "close": decimal_string(selected.close),
            "change_pct": decimal_string(selected_change_pct),
            "direction": price_direction(selected_change_pct),
            "oi_start": decimal_string(oi_start.sum_open_interest) if oi_start else None,
            "oi_end": decimal_string(oi_end.sum_open_interest) if oi_end else None,
            "oi_change": decimal_string(oi_change),
            "oi_change_pct": decimal_string(
                _change_pct(
                    oi_start.sum_open_interest if oi_start else None,
                    oi_end.sum_open_interest if oi_end else None,
                )
            ),
            "oi_boundaries_available": oi_start is not None and oi_end is not None,
        }
    return {
        "status": "complete" if len(klines) == EXPECTED_PRICE_HOURS and not missing and len(safe) == EXPECTED_PRICE_HOURS else ("partial" if klines else "unavailable"),
        "actual_count": len(klines),
        "missing_open_times": [utc_iso(value) for value in missing],
        "daily_change_pct": decimal_string(daily_change_pct),
        "daily_direction": daily_direction,
        "largest_absolute_hour": largest,
    }


def _funding_record(record: FundingRate, *, include_judgment: bool = True) -> dict:
    return {
        "id": record.pk,
        "funding_time": utc_iso(record.funding_time),
        "funding_rate": decimal_string(record.funding_rate),
        "mark_price": decimal_string(record.mark_price),
        "rate_type": record.rate_type,
        "crowding": funding_crowding(record.funding_rate) if include_judgment else None,
    }


def calculate_funding_interval(
    label: str,
    range_start: datetime,
    range_end: datetime,
    records: list[FundingRate],
) -> dict:
    expected = [range_start + timedelta(hours=hour) for hour in (0, 8, 16)]
    missing = [
        value for value in expected
        if not any(
            value <= record.funding_time < value + timedelta(minutes=1)
            for record in records
        )
    ]
    invalid = [
        record
        for record in records
        if not record.funding_rate.is_finite()
        or (
            record.mark_price is not None
            and (not record.mark_price.is_finite() or record.mark_price <= 0)
        )
    ]
    duplicate_slots = [
        value
        for value in expected
        if sum(
            value <= record.funding_time < value + timedelta(minutes=1)
            for record in records
        )
        > 1
    ]
    complete = not missing and not invalid and not duplicate_slots
    status = "complete" if complete else ("partial" if records else "unavailable")
    first = records[0] if records else None
    last = records[-1] if records else None
    minimum = min(records, key=lambda row: (row.funding_rate, row.funding_time)) if records else None
    maximum = max(records, key=lambda row: row.funding_rate) if records else None
    largest_absolute = None
    for record in records:
        if largest_absolute is None or abs(record.funding_rate) > abs(largest_absolute.funding_rate):
            largest_absolute = record
    observed_average = (
        sum((record.funding_rate for record in records), Decimal("0")) / len(records)
        if records else None
    )
    observed_net_change = last.funding_rate - first.funding_rate if first and last else None
    average = observed_average if complete else None
    net_change = observed_net_change if complete else None
    reasons = []
    if missing:
        reasons.append("缺少预期结算点：" + "、".join(utc_iso(value) for value in missing))
    if duplicate_slots:
        reasons.append("同一预期结算点存在重复记录。")
    if invalid:
        reasons.append("存在非法 Funding 数值。")
    if not complete and records:
        reasons.append("覆盖不足，停止输出平均、趋势、方向和拥挤判断。")
    if not reasons:
        reasons.append("三个预期 8 小时结算点均存在且合法。")
    return {
        "label": label,
        "status": status,
        "status_reason": " ".join(reasons),
        "range_start": utc_iso(range_start),
        "range_end": utc_iso(range_end),
        "expected_count": 3,
        "actual_count": len(records),
        "missing_funding_times": [utc_iso(value) for value in missing],
        "duplicate_funding_times": [utc_iso(value) for value in duplicate_slots],
        "invalid_record_ids": [record.pk for record in invalid],
        "records": [
            _funding_record(record, include_judgment=complete) for record in records
        ],
        "first": _funding_record(first, include_judgment=complete) if first else None,
        "last": _funding_record(last, include_judgment=complete) if last else None,
        "minimum": _funding_record(minimum, include_judgment=complete) if minimum else None,
        "maximum": _funding_record(maximum, include_judgment=complete) if maximum else None,
        "average": decimal_string(average),
        "net_change": decimal_string(net_change),
        "trend": _signed_direction(net_change) if net_change is not None else None,
        "largest_absolute": (
            _funding_record(largest_absolute, include_judgment=complete)
            if largest_absolute
            else None
        ),
        "average_direction": funding_crowding(average) if average is not None else None,
    }


def _rule_snapshot() -> dict:
    return {
        "version": RULE_VERSION,
        "timezone": "UTC",
        "oi": {
            "period": "1h",
            "expected_boundary_count": EXPECTED_OI_POINTS,
            "expansion_threshold_pct": decimal_string(OI_THRESHOLD_PCT),
            "contraction_threshold_pct": decimal_string(-OI_THRESHOLD_PCT),
            "neutral_semantics": "absolute_change_below_threshold",
            "timestamp_semantics": "period_end",
        },
        "price": {
            "up_threshold_pct": decimal_string(PRICE_THRESHOLD_PCT),
            "down_threshold_pct": decimal_string(-PRICE_THRESHOLD_PCT),
            "largest_hour_tie_break": "earliest_open_time",
        },
        "funding": {
            "interval_semantics": "left_closed_right_open",
            "expected_utc_hours": [0, 8, 16],
            "expected_time_tolerance": "from_expected_time_inclusive_to_one_minute_after_exclusive",
            "positive_crowding_threshold": decimal_string(FUNDING_CROWDING_THRESHOLD),
            "negative_crowding_threshold": decimal_string(-FUNDING_CROWDING_THRESHOLD),
            "records": "actual_settlements_only",
        },
        "joint_description": "position_behavior_evidence_not_causal_attribution",
    }


def _build_snapshots(research_case: ResearchCase) -> dict:
    day_start = research_case.event_time.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    data_range_start = day_start - timedelta(days=1)
    data_range_end = day_start + timedelta(days=2)
    oi_points = list(
        OpenInterest.objects.filter(
            exchange=research_case.exchange,
            market_type=research_case.market_type,
            symbol=research_case.symbol,
            period="1h",
            timestamp__gte=day_start,
            timestamp__lte=day_end,
        ).order_by("timestamp")
    )
    klines = list(
        Kline.objects.filter(
            exchange=research_case.exchange,
            market_type=research_case.market_type,
            symbol=research_case.symbol,
            interval=Kline.Interval.ONE_HOUR,
            open_time__gte=day_start,
            open_time__lt=day_end,
        ).order_by("open_time")
    )
    funding_records = list(
        FundingRate.objects.filter(
            exchange=research_case.exchange,
            market_type=research_case.market_type,
            symbol=research_case.symbol,
            funding_time__gte=data_range_start,
            funding_time__lt=data_range_end,
        ).order_by("funding_time")
    )
    oi = calculate_oi_snapshot(oi_points, day_start)
    price = calculate_price_snapshot(
        research_case,
        klines,
        {point.timestamp: point for point in oi_points},
    )
    funding_ranges = (
        ("before", day_start - timedelta(days=1), day_start),
        ("event_day", day_start, day_end),
        ("after", day_end, day_start + timedelta(days=2)),
    )
    funding = []
    for label, start, end in funding_ranges:
        interval_records = [record for record in funding_records if start <= record.funding_time < end]
        funding.append(calculate_funding_interval(label, start, end, interval_records))

    joint_description = None
    joint_limitation = None
    oi_joint_direction = oi["conclusion_direction"]
    price_joint_direction = price["daily_direction"]
    if oi["status"] != "complete":
        joint_limitation = "OI 日级边界不完整，不生成方向性联合描述。"
    elif oi["quantity_value_divergence"]:
        joint_limitation = "OI 数量与名义价值方向分歧，不生成确定性扩张/收缩联合判断。"
    elif oi_joint_direction not in {"expansion", "contraction"}:
        joint_limitation = "OI 数量变化不明显，不生成方向性联合描述。"
    elif price_joint_direction not in {"up", "down"}:
        joint_limitation = "价格变化中性，不生成方向性联合描述。"
    else:
        joint_description = JOINT_DESCRIPTIONS[(price_joint_direction, oi_joint_direction)]

    all_funding_complete = all(item["status"] == "complete" for item in funding)
    any_derivatives = bool(oi_points or funding_records)
    if not any_derivatives:
        status = DerivativesEvidence.Status.UNAVAILABLE
        reasons = ["所需日期没有已保存的 Binance OI 或 Funding 来源记录。"]
    elif oi["status"] == "complete" and all_funding_complete and price["status"] == "complete":
        status = DerivativesEvidence.Status.COMPLETE
        reasons = []
    else:
        status = DerivativesEvidence.Status.PARTIAL
        reasons = [*oi["status_reasons"]]
        reasons.extend(item["status_reason"] for item in funding if item["status"] != "complete")
        if price["status"] != "complete":
            reasons.append("当日 1h K线不完整，最大价格小时事实仅按已有数据展示。")

    calculation = {
        "oi": oi,
        "price": price,
        "funding_intervals": funding,
        "joint_description": joint_description,
        "joint_limitation": joint_limitation,
        "causality_notice": "联合描述仅为仓位行为证据，不是价格异常的确定性原因。",
    }
    coverage = {
        "overall_status": status,
        "reasons": reasons,
        "oi_status": oi["status"],
        "price_hour_status": price["status"],
        "funding_statuses": {item["label"]: item["status"] for item in funding},
    }
    source = {
        "exchange": research_case.exchange,
        "market_type": research_case.market_type,
        "symbol": research_case.symbol,
        "timezone": "UTC",
        "oi": {
            "endpoint": "/futures/data/openInterestHist",
            "period": "1h",
            "availability_limit": "Binance currently exposes only approximately the most recent month",
            "record_ids": [point.pk for point in oi_points],
        },
        "funding": {
            "endpoint": "/fapi/v1/fundingRate",
            "record_type": "actual_settlement",
            "record_ids": [record.pk for record in funding_records],
        },
        "price_hour_record_ids": [kline.pk for kline in klines],
        "research_case_id": research_case.pk,
    }
    return {
        "status": status,
        "status_reason": " ".join(reasons),
        "data_range_start": data_range_start,
        "data_range_end": data_range_end,
        "coverage_snapshot": coverage,
        "calculation_snapshot": calculation,
        "rule_snapshot": _rule_snapshot(),
        "source_snapshot": source,
    }


def generate_derivatives_evidence(research_case_id: int):
    research_case = ResearchCase.objects.get(pk=research_case_id)
    try:
        with transaction.atomic():
            research_case = ResearchCase.objects.select_for_update().get(pk=research_case_id)
            defaults = _build_snapshots(research_case)
            defaults.update({"rule_version": RULE_VERSION, "calculated_at": timezone.now()})
            return DerivativesEvidence.objects.update_or_create(
                research_case=research_case,
                defaults=defaults,
            )
    except Exception as exc:
        day_start = research_case.event_time.astimezone(UTC)
        defaults = {
            "status": DerivativesEvidence.Status.FAILED,
            "rule_version": RULE_VERSION,
            "calculated_at": timezone.now(),
            "data_range_start": day_start - timedelta(days=1),
            "data_range_end": day_start + timedelta(days=2),
            "coverage_snapshot": {"overall_status": "failed", "reasons": ["计算过程失败。"]},
            "calculation_snapshot": {},
            "rule_snapshot": _rule_snapshot(),
            "source_snapshot": {"research_case_id": research_case.pk},
            "status_reason": f"{exc.__class__.__name__}: derivatives evidence calculation failed",
        }
        return DerivativesEvidence.objects.update_or_create(
            research_case=research_case,
            defaults=defaults,
        )
