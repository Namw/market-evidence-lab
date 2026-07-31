from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.market_data.models import Kline

from .models import MarketAnomalyFinding, MarketScanRun


EXCHANGE = Kline.Exchange.BINANCE
MARKET_TYPE = Kline.MarketType.USD_M_FUTURES
SYMBOL = "ETHUSDT"
INTERVAL = Kline.Interval.ONE_DAY
RULES_VERSION = "v1"
VOLUME_BASELINE_DAYS = 20
PRICE_CHANGE_THRESHOLD_PCT = Decimal("5")
VOLUME_RATIO_THRESHOLD = Decimal("2")
WICK_BODY_RATIO_THRESHOLD = Decimal("3")
WICK_RANGE_RATIO_THRESHOLD = Decimal("0.40")
ONE_HUNDRED = Decimal("100")
ONE_DAY = timedelta(days=1)


def rules_snapshot() -> dict[str, object]:
    return {
        "version": RULES_VERSION,
        "scope": {
            "exchange": EXCHANGE,
            "market_type": MARKET_TYPE,
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "timezone": "UTC",
            "range_semantics": "[range_start, range_end)",
            "closed_days_only": True,
        },
        "price_change": {
            "formula": "(close - open) / open * 100",
            "absolute_threshold_pct": str(PRICE_CHANGE_THRESHOLD_PCT),
            "operator": ">=",
        },
        "volume_spike": {
            "volume_field": "volume",
            "baseline_days": VOLUME_BASELINE_DAYS,
            "baseline": "previous consecutive UTC calendar days",
            "current_day_excluded": True,
            "require_all_days": True,
            "ratio_threshold": str(VOLUME_RATIO_THRESHOLD),
            "operator": ">=",
        },
        "long_wick": {
            "require_full_range_positive": True,
            "body_ratio_threshold": str(WICK_BODY_RATIO_THRESHOLD),
            "range_ratio_threshold": str(WICK_RANGE_RATIO_THRESHOLD),
            "operator": "both >=",
            "body_zero_satisfies_body_condition": True,
        },
    }


def _validate_range(range_start: datetime, range_end: datetime) -> None:
    if timezone.is_naive(range_start) or timezone.is_naive(range_end):
        raise ValueError("Market scan range must be timezone-aware.")
    start = range_start.astimezone(UTC)
    end = range_end.astimezone(UTC)
    if any((start.hour, start.minute, start.second, start.microsecond)) or any(
        (end.hour, end.minute, end.second, end.microsecond)
    ):
        raise ValueError("Market scan range must align to UTC day boundaries.")
    if start >= end:
        raise ValueError("range_start must be earlier than range_end")
    if (end - start).days > 366:
        raise ValueError("Market scan range cannot exceed 366 days.")
    closed_boundary = timezone.now().astimezone(UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    if end > closed_boundary:
        raise ValueError("Market scan range must contain only closed UTC days.")


def _expected_days(range_start: datetime, range_end: datetime) -> list[datetime]:
    result = []
    cursor = range_start.astimezone(UTC)
    end = range_end.astimezone(UTC)
    while cursor < end:
        result.append(cursor)
        cursor += ONE_DAY
    return result


def _load_klines(range_start: datetime, range_end: datetime) -> list[Kline]:
    return list(
        Kline.objects.filter(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            interval=INTERVAL,
            open_time__gte=range_start - timedelta(days=VOLUME_BASELINE_DAYS),
            open_time__lt=range_end,
        ).order_by("open_time")
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class Evaluation:
    price_change_pct: Decimal
    amplitude_pct: Decimal
    volume_average_20: Decimal | None
    volume_ratio: Decimal | None
    upper_wick_body_ratio: Decimal | None
    upper_wick_range_ratio: Decimal
    lower_wick_body_ratio: Decimal | None
    lower_wick_range_ratio: Decimal
    signals: list[dict[str, object]]


def _volume_baseline(
    open_time: datetime,
    klines_by_day: dict[datetime, Kline],
) -> Decimal | None:
    volumes = []
    for days_ago in range(VOLUME_BASELINE_DAYS, 0, -1):
        baseline_day = open_time - timedelta(days=days_ago)
        baseline_kline = klines_by_day.get(baseline_day)
        if baseline_kline is None or baseline_kline.volume < 0:
            return None
        volumes.append(baseline_kline.volume)
    average = sum(volumes, Decimal("0")) / Decimal(VOLUME_BASELINE_DAYS)
    return average if average > 0 else None


def _evaluate(kline: Kline, klines_by_day: dict[datetime, Kline]) -> Evaluation:
    if kline.open <= 0 or kline.volume < 0:
        raise InvalidOperation("Unsafe open or volume")

    body = abs(kline.close - kline.open)
    full_range = kline.high - kline.low
    upper_wick = kline.high - max(kline.open, kline.close)
    lower_wick = min(kline.open, kline.close) - kline.low
    if full_range < 0 or upper_wick < 0 or lower_wick < 0:
        raise InvalidOperation("Unsafe OHLC relationships")

    price_change_pct = (kline.close - kline.open) / kline.open * ONE_HUNDRED
    amplitude_pct = full_range / kline.open * ONE_HUNDRED
    upper_wick_body_ratio = upper_wick / body if body != 0 else None
    lower_wick_body_ratio = lower_wick / body if body != 0 else None
    upper_wick_range_ratio = (
        upper_wick / full_range if full_range > 0 else Decimal("0")
    )
    lower_wick_range_ratio = (
        lower_wick / full_range if full_range > 0 else Decimal("0")
    )

    volume_average_20 = _volume_baseline(kline.open_time, klines_by_day)
    volume_ratio = (
        kline.volume / volume_average_20 if volume_average_20 is not None else None
    )
    signals: list[dict[str, object]] = []

    if abs(price_change_pct) >= PRICE_CHANGE_THRESHOLD_PCT:
        direction = "up" if price_change_pct >= 0 else "down"
        signals.append(
            {
                "type": f"abnormal_change_{direction}",
                "direction": direction,
                "metric": {
                    "name": "price_change_pct",
                    "value": _decimal_text(price_change_pct),
                    "unit": "percent",
                },
                "threshold": {
                    "name": "absolute_price_change_pct",
                    "operator": ">=",
                    "value": _decimal_text(PRICE_CHANGE_THRESHOLD_PCT),
                    "unit": "percent",
                },
            }
        )

    if volume_ratio is not None and volume_ratio >= VOLUME_RATIO_THRESHOLD:
        signals.append(
            {
                "type": "volume_spike",
                "direction": None,
                "metric": {
                    "name": "volume_ratio",
                    "value": _decimal_text(volume_ratio),
                    "baseline_average": _decimal_text(volume_average_20),
                },
                "threshold": {
                    "name": "volume_ratio",
                    "operator": ">=",
                    "value": _decimal_text(VOLUME_RATIO_THRESHOLD),
                },
            }
        )

    upper_body_condition = body == 0 or (
        upper_wick_body_ratio is not None
        and upper_wick_body_ratio >= WICK_BODY_RATIO_THRESHOLD
    )
    if (
        full_range > 0
        and upper_body_condition
        and upper_wick_range_ratio >= WICK_RANGE_RATIO_THRESHOLD
    ):
        signals.append(
            {
                "type": "long_upper_wick",
                "direction": "upper",
                "metric": {
                    "upper_wick_body_ratio": (
                        None
                        if upper_wick_body_ratio is None
                        else _decimal_text(upper_wick_body_ratio)
                    ),
                    "upper_wick_range_ratio": _decimal_text(
                        upper_wick_range_ratio
                    ),
                    "body_zero": body == 0,
                },
                "threshold": {
                    "body_ratio_operator": ">=",
                    "body_ratio_value": _decimal_text(WICK_BODY_RATIO_THRESHOLD),
                    "body_zero_allowed": True,
                    "range_ratio_operator": ">=",
                    "range_ratio_value": _decimal_text(WICK_RANGE_RATIO_THRESHOLD),
                },
            }
        )

    lower_body_condition = body == 0 or (
        lower_wick_body_ratio is not None
        and lower_wick_body_ratio >= WICK_BODY_RATIO_THRESHOLD
    )
    if (
        full_range > 0
        and lower_body_condition
        and lower_wick_range_ratio >= WICK_RANGE_RATIO_THRESHOLD
    ):
        signals.append(
            {
                "type": "long_lower_wick",
                "direction": "lower",
                "metric": {
                    "lower_wick_body_ratio": (
                        None
                        if lower_wick_body_ratio is None
                        else _decimal_text(lower_wick_body_ratio)
                    ),
                    "lower_wick_range_ratio": _decimal_text(
                        lower_wick_range_ratio
                    ),
                    "body_zero": body == 0,
                },
                "threshold": {
                    "body_ratio_operator": ">=",
                    "body_ratio_value": _decimal_text(WICK_BODY_RATIO_THRESHOLD),
                    "body_zero_allowed": True,
                    "range_ratio_operator": ">=",
                    "range_ratio_value": _decimal_text(WICK_RANGE_RATIO_THRESHOLD),
                },
            }
        )

    return Evaluation(
        price_change_pct=price_change_pct,
        amplitude_pct=amplitude_pct,
        volume_average_20=volume_average_20,
        volume_ratio=volume_ratio,
        upper_wick_body_ratio=upper_wick_body_ratio,
        upper_wick_range_ratio=upper_wick_range_ratio,
        lower_wick_body_ratio=lower_wick_body_ratio,
        lower_wick_range_ratio=lower_wick_range_ratio,
        signals=signals,
    )


def _safe_error_summary(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: market scan execution failed"


def scan_market_anomalies(
    range_start: datetime,
    range_end: datetime,
    trigger: str = MarketScanRun.Trigger.MANUAL,
) -> MarketScanRun:
    _validate_range(range_start, range_end)
    expected_days = _expected_days(range_start, range_end)
    run = MarketScanRun.objects.create(
        exchange=MarketScanRun.Exchange.BINANCE,
        market_type=MarketScanRun.MarketType.USD_M_FUTURES,
        symbol=SYMBOL,
        interval=MarketScanRun.Interval.ONE_DAY,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        rules_version=RULES_VERSION,
        rules_snapshot=rules_snapshot(),
        status=MarketScanRun.Status.RUNNING,
        expected_count=len(expected_days),
        started_at=timezone.now(),
    )

    try:
        klines = _load_klines(range_start, range_end)
        klines_by_day = {kline.open_time.astimezone(UTC): kline for kline in klines}
        actual_count = 0
        evaluated_count = 0
        missing_count = 0
        skipped_invalid_count = 0
        volume_baseline_unavailable_count = 0
        signal_count = 0
        findings = []

        for open_time in expected_days:
            kline = klines_by_day.get(open_time)
            if kline is None:
                missing_count += 1
                continue
            actual_count += 1
            try:
                evaluation = _evaluate(kline, klines_by_day)
            except (ArithmeticError, InvalidOperation, ValueError):
                skipped_invalid_count += 1
                continue
            evaluated_count += 1
            if evaluation.volume_average_20 is None:
                volume_baseline_unavailable_count += 1
            if not evaluation.signals:
                continue
            signal_count += len(evaluation.signals)
            findings.append(
                MarketAnomalyFinding(
                    run=run,
                    kline=kline,
                    open_time=kline.open_time,
                    open=kline.open,
                    high=kline.high,
                    low=kline.low,
                    close=kline.close,
                    volume=kline.volume,
                    price_change_pct=evaluation.price_change_pct,
                    amplitude_pct=evaluation.amplitude_pct,
                    volume_average_20=evaluation.volume_average_20,
                    volume_ratio=evaluation.volume_ratio,
                    upper_wick_body_ratio=evaluation.upper_wick_body_ratio,
                    upper_wick_range_ratio=evaluation.upper_wick_range_ratio,
                    lower_wick_body_ratio=evaluation.lower_wick_body_ratio,
                    lower_wick_range_ratio=evaluation.lower_wick_range_ratio,
                    signals=evaluation.signals,
                )
            )

        with transaction.atomic():
            MarketAnomalyFinding.objects.bulk_create(findings, batch_size=500)
            run.status = MarketScanRun.Status.SUCCESS
            run.actual_count = actual_count
            run.evaluated_count = evaluated_count
            run.missing_count = missing_count
            run.skipped_invalid_count = skipped_invalid_count
            run.volume_baseline_unavailable_count = (
                volume_baseline_unavailable_count
            )
            run.anomaly_day_count = len(findings)
            run.signal_count = signal_count
            run.finished_at = timezone.now()
            run.save(
                update_fields=[
                    "status",
                    "actual_count",
                    "evaluated_count",
                    "missing_count",
                    "skipped_invalid_count",
                    "volume_baseline_unavailable_count",
                    "anomaly_day_count",
                    "signal_count",
                    "finished_at",
                ]
            )
    except Exception as exc:
        run.status = MarketScanRun.Status.FAILED
        run.error_message = _safe_error_summary(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])

    return run

