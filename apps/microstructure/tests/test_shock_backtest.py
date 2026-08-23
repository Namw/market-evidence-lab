from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from apps.microstructure.models import MarketMinute
from apps.microstructure.shock_backtest import (
    backtest_kline_shocks,
    wilson_interval,
)

START = datetime(2026, 8, 20, tzinfo=UTC)


def minute(
    offset: int,
    *,
    open_price: str = "100",
    close: str = "100",
    high: str | None = None,
    low: str | None = None,
    closed: bool = True,
) -> MarketMinute:
    start = START + timedelta(minutes=offset)
    return MarketMinute(
        symbol="ETHUSDT",
        minute_start=start,
        minute_end=start + timedelta(minutes=1),
        open_price=Decimal(open_price),
        high_price=Decimal(high or max(open_price, close, key=Decimal)),
        low_price=Decimal(low or min(open_price, close, key=Decimal)),
        close_price=Decimal(close),
        kline_closed=closed,
    )


class ShockBacktestTests(SimpleTestCase):
    def test_body_signal_and_exact_future_close_are_counted(self):
        rows = [minute(index) for index in range(11)]
        rows[0].close_price = Decimal("100.4")
        rows[5].close_price = Decimal("100.8")
        rows[5].open_price = Decimal("100.8")
        rows[10].close_price = Decimal("101.2")

        result = backtest_kline_shocks(
            rows,
            thresholds_pct=[Decimal("0.3")],
        )[0]

        self.assertEqual(result.valid_window_count, 6)
        self.assertEqual(result.signal_count, 1)
        self.assertEqual(result.hit_count, 1)
        self.assertEqual(result.continuation_count, 1)
        self.assertEqual(result.baseline_hit_count, 2)
        self.assertAlmostEqual(result.probability, 1.0)

    def test_missing_intermediate_minute_invalidates_window(self):
        rows = [minute(index) for index in (0, 1, 2, 4, 5)]
        rows[0].close_price = Decimal("100.4")

        result = backtest_kline_shocks(
            rows,
            thresholds_pct=[Decimal("0.3")],
        )[0]

        self.assertEqual(result.valid_window_count, 0)
        self.assertEqual(result.signal_count, 0)

    def test_range_metric_can_signal_when_body_does_not(self):
        rows = [minute(index) for index in range(6)]
        rows[0].high_price = Decimal("100.4")
        rows[0].low_price = Decimal("99.9")

        body = backtest_kline_shocks(
            rows,
            thresholds_pct=[Decimal("0.3")],
            signal_metric="body",
        )[0]
        candle_range = backtest_kline_shocks(
            rows,
            thresholds_pct=[Decimal("0.3")],
            signal_metric="range",
        )[0]

        self.assertEqual(body.signal_count, 0)
        self.assertEqual(candle_range.signal_count, 1)

    def test_wilson_interval_is_empty_without_samples(self):
        self.assertIsNone(wilson_interval(0, 0))
