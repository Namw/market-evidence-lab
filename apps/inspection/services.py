from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.market_data.models import FundingRate, Kline, OpenInterest

from .models import (
    DerivativesInspectionRun,
    KlineInspectionRun,
    empty_derivatives_inspection_details,
    empty_inspection_details,
)


EXCHANGE = Kline.Exchange.BINANCE
MARKET_TYPE = Kline.MarketType.USD_M_FUTURES
SUPPORTED_SYMBOL = "ETHUSDT"
INTERVAL_STEPS = {
    Kline.Interval.ONE_DAY: timedelta(days=1),
    Kline.Interval.ONE_HOUR: timedelta(hours=1),
    Kline.Interval.FIVE_MINUTES: timedelta(minutes=5),
}
DETAIL_LIMIT = 200
OI_STEPS = {
    OpenInterest.Period.ONE_HOUR: timedelta(hours=1),
    OpenInterest.Period.FIVE_MINUTES: timedelta(minutes=5),
}
FUNDING_STEP = timedelta(hours=8)
FUNDING_TIME_TOLERANCE = timedelta(minutes=1)


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:1_000]


def _is_aligned(value: datetime, interval: str) -> bool:
    value = value.astimezone(UTC)
    if value.second or value.microsecond:
        return False
    if interval == Kline.Interval.FIVE_MINUTES:
        return value.minute % 5 == 0
    if value.minute:
        return False
    return interval == Kline.Interval.ONE_HOUR or value.hour == 0


def _closed_boundary(now: datetime, interval: str) -> datetime:
    now = now.astimezone(UTC)
    if interval == Kline.Interval.FIVE_MINUTES:
        return now.replace(
            minute=now.minute - now.minute % 5,
            second=0,
            microsecond=0,
        )
    if interval == Kline.Interval.ONE_HOUR:
        return now.replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _validate_inputs(
    symbol: str,
    interval: str,
    range_start: datetime,
    range_end: datetime,
) -> None:
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if interval not in INTERVAL_STEPS:
        raise ValueError(f"Unsupported interval: {interval}")
    if timezone.is_naive(range_start) or timezone.is_naive(range_end):
        raise ValueError("Inspection range datetimes must be timezone-aware.")
    if range_start >= range_end:
        raise ValueError("range_start must be earlier than range_end")
    if not _is_aligned(range_start, interval) or not _is_aligned(range_end, interval):
        raise ValueError(f"Inspection range must align to {interval} UTC boundaries.")
    if range_end > _closed_boundary(timezone.now(), interval):
        raise ValueError("Inspection range must contain only closed klines.")


def _expected_open_times(
    range_start: datetime,
    range_end: datetime,
    step: timedelta,
) -> list[datetime]:
    values = []
    cursor = range_start.astimezone(UTC)
    range_end = range_end.astimezone(UTC)
    while cursor < range_end:
        values.append(cursor)
        cursor += step
    return values


def _compress_missing_ranges(
    missing_open_times: Iterable[datetime],
    step: timedelta,
) -> list[dict[str, object]]:
    sorted_times = sorted(missing_open_times)
    if not sorted_times:
        return []

    ranges: list[dict[str, object]] = []
    range_start = sorted_times[0]
    previous = sorted_times[0]
    count = 1
    for open_time in sorted_times[1:]:
        if open_time == previous + step:
            previous = open_time
            count += 1
            continue
        ranges.append(
            {
                "start": range_start.isoformat(),
                "end": (previous + step).isoformat(),
                "count": count,
            }
        )
        range_start = open_time
        previous = open_time
        count = 1
    ranges.append(
        {
            "start": range_start.isoformat(),
            "end": (previous + step).isoformat(),
            "count": count,
        }
    )
    return ranges


class _DetailCollector:
    def __init__(self, factory=empty_inspection_details, limit: int = DETAIL_LIMIT) -> None:
        self.details = factory()
        self.limit = limit
        self.saved_count = 0

    def add_many(self, key: str, items: Iterable[object]) -> None:
        for item in items:
            if self.saved_count < self.limit:
                self.details[key].append(item)
                self.saved_count += 1
            else:
                self.details["details_truncated"] = True


def _ohlc_rules(kline: Kline) -> list[str]:
    rules = []
    if kline.open <= 0:
        rules.append("open_not_positive")
    if kline.high <= 0:
        rules.append("high_not_positive")
    if kline.low <= 0:
        rules.append("low_not_positive")
    if kline.close <= 0:
        rules.append("close_not_positive")
    if kline.high < kline.open:
        rules.append("high_below_open")
    if kline.high < kline.close:
        rules.append("high_below_close")
    if kline.high < kline.low:
        rules.append("high_below_low")
    if kline.low > kline.open:
        rules.append("low_above_open")
    if kline.low > kline.close:
        rules.append("low_above_close")
    return rules


def _numeric_rules(kline: Kline) -> list[str]:
    rules = []
    for field in (
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ):
        if getattr(kline, field) < 0:
            rules.append(f"{field}_negative")
    return rules


@transaction.atomic
def _perform_inspection(
    *,
    symbol: str,
    interval: str,
    range_start: datetime,
    range_end: datetime,
) -> dict[str, object]:
    step = INTERVAL_STEPS[interval]
    expected_open_times = _expected_open_times(range_start, range_end, step)
    expected_set = set(expected_open_times)
    actual_rows = list(
        Kline.objects.filter(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=symbol,
            interval=interval,
            open_time__gte=range_start,
            open_time__lt=range_end,
        ).order_by("open_time", "id")
    )
    open_time_counts = Counter(row.open_time for row in actual_rows)
    actual_open_times = set(open_time_counts)

    missing_open_times = expected_set - actual_open_times
    duplicate_items = [
        {"open_time": open_time.isoformat(), "count": count}
        for open_time, count in sorted(open_time_counts.items())
        if count > 1
    ]
    duplicate_count = sum(item["count"] - 1 for item in duplicate_items)

    misaligned_rows = [
        row for row in actual_rows if not _is_aligned(row.open_time, interval)
    ]
    invalid_ohlc_count = 0
    invalid_numeric_count = 0
    invalid_close_time_count = 0
    invalid_items = []

    for row in actual_rows:
        rules = []
        ohlc_rules = _ohlc_rules(row)
        numeric_rules = _numeric_rules(row)
        expected_close_time = row.open_time + step - timedelta(milliseconds=1)
        if ohlc_rules:
            invalid_ohlc_count += 1
            rules.extend(ohlc_rules)
        if numeric_rules:
            invalid_numeric_count += 1
            rules.extend(numeric_rules)
        if row.close_time != expected_close_time:
            invalid_close_time_count += 1
            rules.append("close_time_mismatch")
        if rules:
            invalid_items.append(
                {"open_time": row.open_time.isoformat(), "rules": rules}
            )

    collector = _DetailCollector()
    collector.add_many(
        "missing_ranges",
        _compress_missing_ranges(missing_open_times, step),
    )
    collector.add_many("duplicate_open_times", duplicate_items)
    collector.add_many(
        "misaligned_open_times",
        [row.open_time.isoformat() for row in misaligned_rows],
    )
    collector.add_many("invalid_rows", invalid_items)

    return {
        "expected_count": len(expected_open_times),
        "actual_count": len(actual_rows),
        "missing_count": len(missing_open_times),
        "duplicate_count": duplicate_count,
        "misaligned_count": len(misaligned_rows),
        "invalid_ohlc_count": invalid_ohlc_count,
        "invalid_numeric_count": invalid_numeric_count,
        "invalid_close_time_count": invalid_close_time_count,
        "details": collector.details,
    }


def inspect_klines(
    symbol: str,
    interval: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = KlineInspectionRun.Trigger.MANUAL,
    *,
    source_collection_run=None,
) -> KlineInspectionRun:
    _validate_inputs(symbol, interval, range_start, range_end)
    run = KlineInspectionRun.objects.create(
        exchange=KlineInspectionRun.Exchange.BINANCE,
        market_type=KlineInspectionRun.MarketType.USD_M_FUTURES,
        symbol=symbol,
        interval=interval,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        status=KlineInspectionRun.Status.RUNNING,
        quality_status=KlineInspectionRun.QualityStatus.PENDING,
        started_at=timezone.now(),
        source_collection_run=source_collection_run,
    )

    try:
        results = _perform_inspection(
            symbol=symbol,
            interval=interval,
            range_start=range_start,
            range_end=range_end,
        )
        for field, value in results.items():
            setattr(run, field, value)
        issue_count = sum(
            getattr(run, field)
            for field in (
                "missing_count",
                "duplicate_count",
                "misaligned_count",
                "invalid_ohlc_count",
                "invalid_numeric_count",
                "invalid_close_time_count",
            )
        )
        run.status = KlineInspectionRun.Status.SUCCESS
        run.quality_status = (
            KlineInspectionRun.QualityStatus.ISSUES
            if issue_count
            else KlineInspectionRun.QualityStatus.PASSED
        )
    except Exception as exc:  # The run record is the inspection service boundary.
        run.status = KlineInspectionRun.Status.FAILED
        run.quality_status = KlineInspectionRun.QualityStatus.PENDING
        run.error_message = _safe_error_message(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()

    return run


def _validate_derivatives_inputs(
    data_type: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    period: str,
) -> None:
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if data_type not in DerivativesInspectionRun.DataType.values:
        raise ValueError(f"Unsupported derivatives data type: {data_type}")
    if timezone.is_naive(range_start) or timezone.is_naive(range_end):
        raise ValueError("Inspection range datetimes must be timezone-aware.")
    if range_start >= range_end:
        raise ValueError("range_start must be earlier than range_end")
    start = range_start.astimezone(UTC)
    end = range_end.astimezone(UTC)
    if data_type == DerivativesInspectionRun.DataType.OPEN_INTEREST:
        if period not in OI_STEPS:
            raise ValueError(f"Unsupported OI period: {period}")
        step_minutes = int(OI_STEPS[period].total_seconds() // 60)
        if any((start.minute % step_minutes, start.second, start.microsecond, end.minute % step_minutes, end.second, end.microsecond)):
            raise ValueError(f"OI inspection ranges must align to {period} UTC boundaries.")
    elif any((start.minute, start.second, start.microsecond, end.minute, end.second, end.microsecond)):
        raise ValueError("Derivatives inspection ranges must align to UTC hours.")
    if data_type == DerivativesInspectionRun.DataType.FUNDING and (
        start.hour % 8 or end.hour % 8
    ):
        raise ValueError("Funding inspection ranges must align to 8-hour UTC boundaries.")


def _decimal_is_finite(value) -> bool:
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, TypeError, ValueError):
        return False


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _sequence_issues(rows, time_field: str) -> list[dict[str, str]]:
    issues = []
    for previous, current in zip(rows, rows[1:]):
        previous_time = getattr(previous, time_field)
        current_time = getattr(current, time_field)
        if current_time < previous_time:
            issues.append(
                {
                    "previous": _iso(previous_time),
                    "current": _iso(current_time),
                }
            )
    return issues


def _inspect_open_interest_rows(
    rows: list[OpenInterest],
    range_start: datetime,
    range_end: datetime,
    period: str = OpenInterest.Period.ONE_HOUR,
) -> dict[str, object]:
    step = OI_STEPS[period]
    expected = _expected_open_times(range_start, range_end, step)
    expected_set = set(expected)
    timestamps = [row.timestamp for row in rows]
    timestamp_counts = Counter(timestamps)
    actual_set = set(timestamps)
    missing = expected_set - actual_set
    duplicate_items = [
        {"timestamp": _iso(timestamp), "count": count}
        for timestamp, count in sorted(timestamp_counts.items())
        if count > 1
    ]
    duplicate_count = sum(item["count"] - 1 for item in duplicate_items)
    sequence_issues = _sequence_issues(rows, "timestamp")
    misaligned = [timestamp for timestamp in timestamps if timestamp not in expected_set]
    invalid_items = []
    for row in rows:
        rules = []
        if not _decimal_is_finite(row.sum_open_interest):
            rules.append("sum_open_interest_not_finite")
        elif row.sum_open_interest < 0:
            rules.append("sum_open_interest_negative")
        if not _decimal_is_finite(row.sum_open_interest_value):
            rules.append("sum_open_interest_value_not_finite")
        elif row.sum_open_interest_value < 0:
            rules.append("sum_open_interest_value_negative")
        if rules:
            invalid_items.append({"timestamp": _iso(row.timestamp), "rules": rules})

    collector = _DetailCollector(empty_derivatives_inspection_details)
    collector.details["no_data"] = not rows
    collector.add_many("missing_ranges", _compress_missing_ranges(missing, step))
    collector.add_many("duplicate_timestamps", duplicate_items)
    collector.add_many("sequence_issues", sequence_issues)
    collector.add_many("misaligned_timestamps", [_iso(value) for value in misaligned])
    collector.add_many("invalid_rows", invalid_items)
    counts = {
        "empty_count": 1 if not rows else 0,
        "missing_count": len(missing),
        "duplicate_count": duplicate_count,
        "sequence_issue_count": len(sequence_issues),
        "misaligned_count": len(misaligned),
        "invalid_numeric_count": len(invalid_items),
    }
    return {
        "expected_count": len(expected),
        "actual_count": len(rows),
        "issue_count": sum(counts.values()),
        **counts,
        "details": collector.details,
    }


def _funding_expected_times(
    range_start: datetime,
    range_end: datetime,
) -> list[datetime]:
    return _expected_open_times(range_start, range_end, FUNDING_STEP)


def _funding_slot(
    funding_time: datetime,
    expected: list[datetime],
) -> datetime | None:
    for expected_time in expected:
        if expected_time <= funding_time < expected_time + FUNDING_TIME_TOLERANCE:
            return expected_time
    return None


def _inspect_funding_rows(
    rows: list[FundingRate],
    range_start: datetime,
    range_end: datetime,
) -> dict[str, object]:
    expected = _funding_expected_times(range_start, range_end)
    records_by_slot: dict[datetime, list[FundingRate]] = {value: [] for value in expected}
    misaligned = []
    for row in rows:
        slot = _funding_slot(row.funding_time, expected)
        if slot is None:
            misaligned.append(row.funding_time)
        else:
            records_by_slot[slot].append(row)
    missing = [slot for slot, slot_rows in records_by_slot.items() if not slot_rows]
    duplicate_items = [
        {
            "expected_time": _iso(slot),
            "count": len(slot_rows),
            "record_times": [_iso(row.funding_time) for row in slot_rows],
        }
        for slot, slot_rows in records_by_slot.items()
        if len(slot_rows) > 1
    ]
    duplicate_count = sum(item["count"] - 1 for item in duplicate_items)
    sequence_issues = _sequence_issues(rows, "funding_time")
    invalid_items = []
    for row in rows:
        rules = []
        if not _decimal_is_finite(row.funding_rate):
            rules.append("funding_rate_not_finite")
        if row.mark_price is not None:
            if not _decimal_is_finite(row.mark_price):
                rules.append("mark_price_not_finite")
            elif row.mark_price <= 0:
                rules.append("mark_price_not_positive")
        if rules:
            invalid_items.append({"funding_time": _iso(row.funding_time), "rules": rules})

    collector = _DetailCollector(empty_derivatives_inspection_details)
    collector.details["no_data"] = not rows
    collector.add_many("missing_settlements", [_iso(value) for value in missing])
    collector.add_many("duplicate_timestamps", duplicate_items)
    collector.add_many("sequence_issues", sequence_issues)
    collector.add_many("misaligned_timestamps", [_iso(value) for value in misaligned])
    collector.add_many("invalid_rows", invalid_items)
    counts = {
        "empty_count": 1 if not rows else 0,
        "missing_count": len(missing),
        "duplicate_count": duplicate_count,
        "sequence_issue_count": len(sequence_issues),
        "misaligned_count": len(misaligned),
        "invalid_numeric_count": len(invalid_items),
    }
    return {
        "expected_count": len(expected),
        "actual_count": len(rows),
        "issue_count": sum(counts.values()),
        **counts,
        "details": collector.details,
    }


def _perform_derivatives_inspection(
    *,
    data_type: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    period: str,
) -> dict[str, object]:
    common_filters = {
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": symbol,
    }
    if data_type == DerivativesInspectionRun.DataType.OPEN_INTEREST:
        rows = list(
            OpenInterest.objects.filter(
                **common_filters,
                period=period,
                timestamp__gte=range_start,
                timestamp__lt=range_end,
            ).order_by("timestamp", "id")
        )
        return _inspect_open_interest_rows(rows, range_start, range_end, period)
    rows = list(
        FundingRate.objects.filter(
            **common_filters,
            funding_time__gte=range_start,
            funding_time__lt=range_end,
        ).order_by("funding_time", "id")
    )
    return _inspect_funding_rows(rows, range_start, range_end)


def _inspect_derivatives(
    data_type: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str,
    period: str,
    *,
    source_collection_run=None,
) -> DerivativesInspectionRun:
    _validate_derivatives_inputs(data_type, symbol, range_start, range_end, period)
    run = DerivativesInspectionRun.objects.create(
        data_type=data_type,
        exchange=DerivativesInspectionRun.Exchange.BINANCE,
        market_type=DerivativesInspectionRun.MarketType.USD_M_FUTURES,
        symbol=symbol,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        status=DerivativesInspectionRun.Status.RUNNING,
        quality_status=DerivativesInspectionRun.QualityStatus.PENDING,
        started_at=timezone.now(),
        source_collection_run=source_collection_run,
    )
    try:
        results = _perform_derivatives_inspection(
            data_type=data_type,
            symbol=symbol,
            range_start=range_start,
            range_end=range_end,
            period=period,
        )
        for field, value in results.items():
            setattr(run, field, value)
        run.status = DerivativesInspectionRun.Status.SUCCESS
        run.quality_status = (
            DerivativesInspectionRun.QualityStatus.ISSUES
            if run.issue_count
            else DerivativesInspectionRun.QualityStatus.PASSED
        )
    except Exception as exc:
        run.status = DerivativesInspectionRun.Status.FAILED
        run.quality_status = DerivativesInspectionRun.QualityStatus.PENDING
        run.error_message = _safe_error_message(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run


def inspect_open_interest(
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = DerivativesInspectionRun.Trigger.MANUAL,
    *,
    period: str = OpenInterest.Period.ONE_HOUR,
    source_collection_run=None,
) -> DerivativesInspectionRun:
    return _inspect_derivatives(
        DerivativesInspectionRun.DataType.OPEN_INTEREST,
        symbol,
        range_start,
        range_end,
        trigger,
        period,
        source_collection_run=source_collection_run,
    )


def inspect_funding_rates(
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = DerivativesInspectionRun.Trigger.MANUAL,
    *,
    source_collection_run=None,
) -> DerivativesInspectionRun:
    return _inspect_derivatives(
        DerivativesInspectionRun.DataType.FUNDING,
        symbol,
        range_start,
        range_end,
        trigger,
        "actual",
        source_collection_run=source_collection_run,
    )
