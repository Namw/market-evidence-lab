from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Iterable, Literal

from .models import MarketMinute

SignalMetric = Literal["body", "range"]


@dataclass(frozen=True, slots=True)
class ShockBacktestResult:
    threshold_pct: Decimal
    valid_window_count: int
    signal_count: int
    hit_count: int
    continuation_count: int
    reversal_count: int
    positive_signal_count: int
    negative_signal_count: int
    neutral_signal_count: int
    neutral_hit_count: int
    baseline_hit_count: int

    @property
    def probability(self) -> float | None:
        return _ratio(self.hit_count, self.signal_count)

    @property
    def baseline_probability(self) -> float | None:
        return _ratio(self.baseline_hit_count, self.valid_window_count)

    @property
    def lift(self) -> float | None:
        probability = self.probability
        baseline = self.baseline_probability
        if probability is None or not baseline:
            return None
        return probability / baseline

    @property
    def confidence_interval_95(self) -> tuple[float, float] | None:
        return wilson_interval(self.hit_count, self.signal_count)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> tuple[float, float] | None:
    """Return a 95% Wilson score interval for a binomial probability."""
    if total <= 0:
        return None
    probability = successes / total
    denominator = 1 + z * z / total
    center = (probability + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1 - probability) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _return(start: Decimal, end: Decimal) -> Decimal | None:
    start = Decimal(start)
    end = Decimal(end)
    if start <= 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return end / start - Decimal(1)


def _signal_move(row: MarketMinute, metric: SignalMetric) -> Decimal | None:
    if row.open_price is None or row.close_price is None:
        return None
    body_return = _return(row.open_price, row.close_price)
    if metric == "body":
        return body_return
    if row.high_price is None or row.low_price is None or row.open_price <= 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return (Decimal(row.high_price) - Decimal(row.low_price)) / Decimal(
            row.open_price
        )


def backtest_kline_shocks(
    rows: Iterable[MarketMinute],
    *,
    thresholds_pct: Iterable[Decimal],
    horizon_minutes: int = 5,
    signal_metric: SignalMetric = "body",
) -> list[ShockBacktestResult]:
    """Backtest large 1m candles against an exact, gap-free future return.

    ``body`` signals use ``abs(close / open - 1)``. ``range`` signals use
    ``(high - low) / open``; continuation versus reversal is still determined
    by the candle body's direction. The future outcome always uses
    ``close[t + horizon] / close[t] - 1``.
    """
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    if signal_metric not in {"body", "range"}:
        raise ValueError("signal_metric must be 'body' or 'range'")

    thresholds = [Decimal(value) for value in thresholds_pct]
    if not thresholds or any(value <= 0 for value in thresholds):
        raise ValueError("thresholds_pct must contain positive values")

    ordered_rows = sorted(rows, key=lambda row: row.minute_start)
    by_start = {row.minute_start: row for row in ordered_rows}
    step = timedelta(minutes=1)
    horizon = timedelta(minutes=horizon_minutes)
    observations: list[tuple[Decimal, Decimal, Decimal]] = []

    for row in ordered_rows:
        window = [
            by_start.get(row.minute_start + offset * step)
            for offset in range(horizon_minutes + 1)
        ]
        if not all(
            item is not None
            and item.kline_closed
            and item.close_price is not None
            for item in window
        ):
            continue
        target = by_start[row.minute_start + horizon]
        signal_move = _signal_move(row, signal_metric)
        body_return = _return(row.open_price, row.close_price)
        future_return = _return(row.close_price, target.close_price)
        if signal_move is None or body_return is None or future_return is None:
            continue
        observations.append((signal_move, body_return, future_return))

    results: list[ShockBacktestResult] = []
    for threshold_pct in thresholds:
        threshold = threshold_pct / Decimal(100)
        signals = [
            observation
            for observation in observations
            if abs(observation[0]) >= threshold
        ]
        hits = [
            observation
            for observation in signals
            if abs(observation[2]) >= threshold
        ]
        continuation_count = sum(
            body_return != 0 and (body_return > 0) == (future_return > 0)
            for _, body_return, future_return in hits
        )
        reversal_count = sum(
            body_return != 0 and (body_return > 0) != (future_return > 0)
            for _, body_return, future_return in hits
        )
        results.append(
            ShockBacktestResult(
                threshold_pct=threshold_pct,
                valid_window_count=len(observations),
                signal_count=len(signals),
                hit_count=len(hits),
                continuation_count=continuation_count,
                reversal_count=reversal_count,
                positive_signal_count=sum(
                    body_return > 0 for _, body_return, _ in signals
                ),
                negative_signal_count=sum(
                    body_return < 0 for _, body_return, _ in signals
                ),
                neutral_signal_count=sum(
                    body_return == 0 for _, body_return, _ in signals
                ),
                neutral_hit_count=sum(
                    body_return == 0 for _, body_return, _ in hits
                ),
                baseline_hit_count=sum(
                    abs(future_return) >= threshold
                    for _, _, future_return in observations
                ),
            )
        )
    return results
