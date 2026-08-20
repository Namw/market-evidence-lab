from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.microstructure.models import MarketMinute
from apps.microstructure.research import (
    build_trade_intensity_research,
    build_trade_imbalance_research,
    calculate_trade_intensity,
    calculate_future_5m_returns,
    refresh_future_5m_returns,
)

START = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)


def minute(
    offset: int,
    *,
    close: str = "100",
    buy: str = "600",
    sell: str = "400",
    volume: str | None = None,
    closed: bool = True,
) -> MarketMinute:
    start = START + timedelta(minutes=offset)
    return MarketMinute.objects.create(
        symbol="ETHUSDT",
        minute_start=start,
        minute_end=start + timedelta(minutes=1),
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
        quote_volume=(
            Decimal(volume)
            if volume is not None
            else Decimal(buy) + Decimal(sell)
        ),
        taker_buy_quote=buy,
        taker_sell_quote=sell,
        delta_quote=Decimal(buy) - Decimal(sell),
        kline_closed=closed,
    )


class FutureReturnResearchTests(TestCase):
    def test_future_return_uses_exact_five_minute_close(self):
        rows = [minute(index, close=str(100 + index)) for index in range(6)]

        values = calculate_future_5m_returns(rows)

        self.assertEqual(values[rows[0].pk], Decimal("0.050000000000000000"))
        self.assertIsNone(values[rows[1].pk])

    def test_future_return_is_empty_when_an_intermediate_minute_is_missing(self):
        rows = [minute(index) for index in (0, 1, 2, 4, 5)]

        values = calculate_future_5m_returns(rows)

        self.assertIsNone(values[rows[0].pk])

    def test_future_return_is_empty_when_an_intermediate_kline_is_not_closed(self):
        rows = [minute(index, closed=index != 3) for index in range(6)]

        values = calculate_future_5m_returns(rows)

        self.assertIsNone(values[rows[0].pk])

    def test_backfill_sets_valid_labels_and_leaves_tail_empty(self):
        rows = [minute(index, close=str(100 + index)) for index in range(8)]

        changed = refresh_future_5m_returns(symbol="ETHUSDT")

        self.assertEqual(changed, 3)
        rows[0].refresh_from_db()
        rows[3].refresh_from_db()
        self.assertEqual(
            rows[0].future_5m_return,
            Decimal("0.050000000000000000"),
        )
        self.assertIsNone(rows[3].future_5m_return)


class TradeImbalanceDecileTests(TestCase):
    def test_research_uses_time_split_purge_and_training_cutpoints(self):
        for index in range(100):
            buy = Decimal(index + 1)
            sell = Decimal(100 - index)
            row = minute(index, buy=str(buy), sell=str(sell))
            row.future_5m_return = Decimal(index - 50) / Decimal("100000")
            row.save(update_fields=["future_5m_return"])

        result = build_trade_imbalance_research("ETHUSDT")

        self.assertEqual(result["sample_count"], 100)
        self.assertEqual(result["discovery_count"], 65)
        self.assertEqual(result["purged_count"], 5)
        self.assertEqual(result["validation_count"], 30)
        self.assertEqual(
            sum(group["discovery"]["sample_count"] for group in result["groups"]),
            65,
        )
        self.assertEqual(
            sum(group["validation"]["sample_count"] for group in result["groups"]),
            30,
        )
        self.assertEqual(result["groups"][-1]["validation"]["sample_count"], 30)


class TradeIntensityTests(TestCase):
    def test_intensity_uses_only_the_previous_sixty_minute_median(self):
        rows = [
            minute(index, volume=str(index + 1))
            for index in range(61)
        ]

        values = calculate_trade_intensity(rows)

        self.assertIsNone(values[rows[59].pk])
        self.assertEqual(values[rows[60].pk], Decimal("2.000000000000000000"))

    def test_intensity_is_empty_when_prior_window_has_a_gap(self):
        rows = [minute(index, volume="100") for index in range(60)]
        current = minute(61, volume="200")
        rows.append(current)

        values = calculate_trade_intensity(rows)

        self.assertIsNone(values[current.pk])

    def test_intensity_is_empty_when_prior_window_contains_unclosed_kline(self):
        rows = [
            minute(index, volume="100", closed=index != 30)
            for index in range(61)
        ]

        values = calculate_trade_intensity(rows)

        self.assertIsNone(values[rows[-1].pk])

    def test_intensity_research_reuses_time_split_and_training_cutpoints(self):
        for index in range(160):
            row = minute(index, volume=str(100 + index))
            row.future_5m_return = Decimal(index - 80) / Decimal("100000")
            row.save(update_fields=["future_5m_return"])

        result = build_trade_intensity_research("ETHUSDT")

        self.assertEqual(result["metric"]["key"], "trade_intensity")
        self.assertEqual(result["sample_count"], 100)
        self.assertEqual(result["discovery_count"], 65)
        self.assertEqual(result["purged_count"], 5)
        self.assertEqual(result["validation_count"], 30)
        self.assertEqual(
            sum(group["discovery"]["sample_count"] for group in result["groups"]),
            65,
        )
        self.assertEqual(
            sum(group["validation"]["sample_count"] for group in result["groups"]),
            30,
        )
