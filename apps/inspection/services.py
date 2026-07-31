from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.market_data.models import Kline

from .models import KlineInspectionRun, empty_inspection_details


EXCHANGE = Kline.Exchange.BINANCE
MARKET_TYPE = Kline.MarketType.USD_M_FUTURES
SUPPORTED_SYMBOL = "ETHUSDT"
INTERVAL_STEPS = {
    Kline.Interval.ONE_DAY: timedelta(days=1),
    Kline.Interval.ONE_HOUR: timedelta(hours=1),
}
DETAIL_LIMIT = 200


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:1_000]


def _is_aligned(value: datetime, interval: str) -> bool:
    value = value.astimezone(UTC)
    if value.minute or value.second or value.microsecond:
        return False
    return interval == Kline.Interval.ONE_HOUR or value.hour == 0


def _closed_boundary(now: datetime, interval: str) -> datetime:
    now = now.astimezone(UTC)
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
    def __init__(self, limit: int = DETAIL_LIMIT) -> None:
        self.details = empty_inspection_details()
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
