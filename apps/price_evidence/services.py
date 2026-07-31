from datetime import UTC, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.market_data.models import Kline
from apps.research_cases.models import ResearchCase

from .models import PriceEvidence


EXPECTED_COUNT = 24
HUNDRED = Decimal("100")
EIGHTY_PERCENT = Decimal("0.8")


def decimal_string(value):
    return format(value, "f")


def utc_iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _expected_open_times(range_start):
    return [range_start + timedelta(hours=hour) for hour in range(EXPECTED_COUNT)]


def _snapshot_kline(kline):
    return {
        "open_time": utc_iso(kline.open_time),
        "open": decimal_string(kline.open),
        "high": decimal_string(kline.high),
        "low": decimal_string(kline.low),
        "close": decimal_string(kline.close),
        "volume": decimal_string(kline.volume),
    }


def _safety_issues(klines, expected_times):
    issues = []
    expected_set = set(expected_times)
    for kline in klines:
        prefix = utc_iso(kline.open_time)
        if kline.open_time not in expected_set:
            issues.append(f"unexpected_open_time:{prefix}")
        if min(kline.open, kline.high, kline.low, kline.close) <= 0:
            issues.append(f"non_positive_ohlc:{prefix}")
        if kline.high < max(kline.open, kline.close, kline.low):
            issues.append(f"invalid_high:{prefix}")
        if kline.low > min(kline.open, kline.close, kline.high):
            issues.append(f"invalid_low:{prefix}")
        if kline.volume < 0:
            issues.append(f"negative_volume:{prefix}")
    return issues


def _aggregate(klines):
    if not klines:
        return {field: None for field in ("open", "high", "low", "close", "volume")}
    return {
        "open": klines[0].open,
        "high": max(kline.high for kline in klines),
        "low": min(kline.low for kline in klines),
        "close": klines[-1].close,
        "volume": sum((kline.volume for kline in klines), Decimal("0")),
    }


def _daily_consistency(research_case, aggregate, comparable, safety_issues):
    snapshot = {
        "safe_input": not safety_issues,
        "safety_issues": safety_issues,
    }
    for field in ("open", "high", "low", "close", "volume"):
        daily_value = getattr(research_case, field)
        aggregate_value = aggregate[field]
        snapshot[field] = {
            "daily_snapshot_value": decimal_string(daily_value),
            "hourly_aggregate_value": (
                None if aggregate_value is None else decimal_string(aggregate_value)
            ),
            "matches": bool(comparable and aggregate_value == daily_value),
        }
    return snapshot


def _high_low_metrics(klines):
    highest = max(klines, key=lambda kline: kline.high)
    lowest = min(klines, key=lambda kline: kline.low)
    if highest.open_time < lowest.open_time:
        order = "high_before_low"
    elif lowest.open_time < highest.open_time:
        order = "low_before_high"
    else:
        order = "same_hour"
    return {
        "highest": {
            "open_time": utc_iso(highest.open_time),
            "price": decimal_string(highest.high),
        },
        "lowest": {
            "open_time": utc_iso(lowest.open_time),
            "price": decimal_string(lowest.low),
        },
        "order": order,
    }


def _hour_direction_metrics(klines):
    counts = {"up": 0, "down": 0, "flat": 0}
    largest = None
    largest_absolute_change = None
    for kline in klines:
        if kline.close > kline.open:
            direction = "up"
        elif kline.close < kline.open:
            direction = "down"
        else:
            direction = "flat"
        counts[direction] += 1
        change_pct = (kline.close - kline.open) / kline.open * HUNDRED
        absolute_change = abs(change_pct)
        if largest is None or absolute_change > largest_absolute_change:
            largest = {
                "open_time": utc_iso(kline.open_time),
                "direction": direction,
                "change_pct": decimal_string(change_pct),
            }
            largest_absolute_change = absolute_change
    return counts, largest


def _net_change_eighty_percent(research_case, klines):
    net_change = research_case.close - research_case.open
    if net_change == 0:
        return {
            "available": False,
            "reason": "flat_day",
            "target_price": None,
            "hourly_open_time": None,
        }
    target = research_case.open + net_change * EIGHTY_PERCENT
    for kline in klines:
        reached = kline.close >= target if net_change > 0 else kline.close <= target
        if reached:
            return {
                "available": True,
                "target_price": decimal_string(target),
                "hourly_open_time": utc_iso(kline.open_time),
                "close_observed_at": utc_iso(kline.open_time + timedelta(hours=1)),
            }
    return {
        "available": False,
        "reason": "not_reached",
        "target_price": decimal_string(target),
        "hourly_open_time": None,
    }


def _sequence_excursion(points, mode):
    anchor = points[0]
    best = {
        "amount": Decimal("0"),
        "from_time": anchor[0],
        "to_time": anchor[0],
        "base_value": anchor[1],
    }
    for point in points[1:]:
        if mode == "drawdown":
            amount = anchor[1] - point[1]
            if point[1] > anchor[1]:
                anchor = point
        else:
            amount = point[1] - anchor[1]
            if point[1] < anchor[1]:
                anchor = point
        if amount > best["amount"]:
            best = {
                "amount": amount,
                "from_time": anchor[0],
                "to_time": point[0],
                "base_value": anchor[1],
            }
    percentage = (
        best["amount"] / best["base_value"] * HUNDRED
        if best["base_value"] > 0
        else None
    )
    return {
        "amount": decimal_string(best["amount"]),
        "percentage": None if percentage is None else decimal_string(percentage),
        "from_time": utc_iso(best["from_time"]),
        "to_time": utc_iso(best["to_time"]),
    }


def _drawdown_and_rebound(research_case, klines):
    points = [(research_case.event_time, research_case.open)] + [
        (kline.open_time + timedelta(hours=1), kline.close) for kline in klines
    ]
    return {
        "max_drawdown": _sequence_excursion(points, "drawdown"),
        "max_rebound": _sequence_excursion(points, "rebound"),
    }


def _close_retention_rate(research_case):
    if research_case.close > research_case.open:
        denominator = research_case.high - research_case.open
        direction = "up"
        numerator = research_case.close - research_case.open
    elif research_case.close < research_case.open:
        denominator = research_case.open - research_case.low
        direction = "down"
        numerator = research_case.open - research_case.close
    else:
        return {"direction": "flat", "rate_pct": None}
    if denominator <= 0:
        return {"direction": direction, "rate_pct": None}
    return {
        "direction": direction,
        "rate_pct": decimal_string(numerator / denominator * HUNDRED),
    }


def _volume_distribution(klines, aggregate_volume):
    if aggregate_volume <= 0:
        return {"available": False, "reason": "non_positive_total_volume"}
    maximum = max(klines, key=lambda kline: kline.volume)
    top_three = sorted(
        klines,
        key=lambda kline: (-kline.volume, kline.open_time),
    )[:3]
    top_three_volume = sum(
        (kline.volume for kline in top_three),
        Decimal("0"),
    )
    return {
        "available": True,
        "maximum_hour": {
            "open_time": utc_iso(maximum.open_time),
            "volume": decimal_string(maximum.volume),
        },
        "top_three_open_times": [utc_iso(kline.open_time) for kline in top_three],
        "top_three_volume": decimal_string(top_three_volume),
        "top_three_share_pct": decimal_string(
            top_three_volume / aggregate_volume * HUNDRED
        ),
    }


def _metrics(research_case, klines, aggregate):
    hour_counts, largest_hourly_change = _hour_direction_metrics(klines)
    return {
        "high_low": _high_low_metrics(klines),
        "hour_counts": hour_counts,
        "largest_hourly_change": largest_hourly_change,
        "net_change_eighty_percent": _net_change_eighty_percent(
            research_case,
            klines,
        ),
        **_drawdown_and_rebound(research_case, klines),
        "close_retention": _close_retention_rate(research_case),
        "volume_distribution": _volume_distribution(klines, aggregate["volume"]),
    }


@transaction.atomic
def generate_price_evidence(research_case_id):
    research_case = ResearchCase.objects.select_for_update().get(pk=research_case_id)
    range_start = research_case.event_time
    range_end = range_start + timedelta(days=1)
    expected_times = _expected_open_times(range_start)
    klines = list(
        Kline.objects.filter(
            exchange=research_case.exchange,
            market_type=research_case.market_type,
            symbol=research_case.symbol,
            interval=Kline.Interval.ONE_HOUR,
            open_time__gte=range_start,
            open_time__lt=range_end,
        ).order_by("open_time")
    )
    actual_times = {kline.open_time for kline in klines}
    missing_times = [time for time in expected_times if time not in actual_times]
    safety_issues = _safety_issues(klines, expected_times)
    aggregate = _aggregate(klines)
    comparable = len(klines) == EXPECTED_COUNT and not missing_times
    consistency = _daily_consistency(
        research_case,
        aggregate,
        comparable,
        safety_issues,
    )
    all_fields_match = all(
        consistency[field]["matches"]
        for field in ("open", "high", "low", "close", "volume")
    )

    if not klines:
        quality_status = PriceEvidence.QualityStatus.UNAVAILABLE
    elif missing_times:
        quality_status = PriceEvidence.QualityStatus.PARTIAL
    elif not comparable or safety_issues or not all_fields_match:
        quality_status = PriceEvidence.QualityStatus.INCONSISTENT
    else:
        quality_status = PriceEvidence.QualityStatus.COMPLETE

    metrics = (
        _metrics(research_case, klines, aggregate)
        if quality_status == PriceEvidence.QualityStatus.COMPLETE
        else {}
    )
    defaults = {
        "calculation_version": "v1",
        "range_start": range_start,
        "range_end": range_end,
        "quality_status": quality_status,
        "expected_count": EXPECTED_COUNT,
        "actual_count": len(klines),
        "missing_open_times": [utc_iso(value) for value in missing_times],
        "hourly_klines_snapshot": [_snapshot_kline(kline) for kline in klines],
        "metrics_snapshot": metrics,
        "daily_consistency_snapshot": consistency,
        "generated_at": timezone.now(),
    }
    return PriceEvidence.objects.update_or_create(
        research_case=research_case,
        defaults=defaults,
    )
