from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.microstructure.models import MarketMinute
from apps.microstructure.research import (
    build_depth_drop_research,
    build_spread_expansion_research,
    build_top5_imbalance_research,
    build_trade_intensity_research,
    build_trade_imbalance_research,
    calculate_spread_expansion,
    calculate_trade_intensity,
    calculate_future_5m_returns,
    depth_drop_ratio,
    refresh_future_5m_returns,
    top5_imbalance,
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
    bid_depth_open: str | None = None,
    ask_depth_open: str | None = None,
    bid_depth_close: str | None = None,
    ask_depth_close: str | None = None,
    book_samples: int = 0,
    coverage: str = "0",
    spread_p95: str | None = None,
    imbalance_top5_mean: str | None = None,
    imbalance_top5_samples: int = 0,
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
        bid_depth_open=bid_depth_open,
        ask_depth_open=ask_depth_open,
        bid_depth_close=bid_depth_close,
        ask_depth_close=ask_depth_close,
        book_sample_count=book_samples,
        coverage_ratio=coverage,
        spread_bps_p95=spread_p95,
        imbalance_top5_mean=imbalance_top5_mean,
        imbalance_top5_sample_count=imbalance_top5_samples,
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


class DepthDropTests(TestCase):
    def test_depth_drop_compares_combined_top20_open_and_close_depth(self):
        row = minute(
            0,
            bid_depth_open="600",
            ask_depth_open="400",
            bid_depth_close="300",
            ask_depth_close="200",
            book_samples=60,
            coverage="1",
        )

        self.assertEqual(depth_drop_ratio(row), Decimal("0.500000000000000000"))

    def test_depth_drop_is_empty_when_coverage_is_below_eighty_percent(self):
        row = minute(
            0,
            bid_depth_open="600",
            ask_depth_open="400",
            bid_depth_close="300",
            ask_depth_close="200",
            book_samples=47,
            coverage="0.799999",
        )

        self.assertIsNone(depth_drop_ratio(row))

    def test_depth_drop_is_empty_without_open_close_or_two_samples(self):
        missing_close = minute(
            0,
            bid_depth_open="600",
            ask_depth_open="400",
            book_samples=60,
            coverage="1",
        )
        one_sample = minute(
            1,
            bid_depth_open="600",
            ask_depth_open="400",
            bid_depth_close="300",
            ask_depth_close="200",
            book_samples=1,
            coverage="1",
        )

        self.assertIsNone(depth_drop_ratio(missing_close))
        self.assertIsNone(depth_drop_ratio(one_sample))

    def test_depth_drop_research_reuses_time_split_and_training_cutpoints(self):
        for index in range(100):
            row = minute(
                index,
                bid_depth_open="600",
                ask_depth_open="400",
                bid_depth_close=str(600 - index * 3),
                ask_depth_close=str(400 - index * 2),
                book_samples=60,
                coverage="1",
            )
            row.future_5m_return = Decimal(index - 50) / Decimal("100000")
            row.save(update_fields=["future_5m_return"])

        result = build_depth_drop_research("ETHUSDT")

        self.assertEqual(result["metric"]["key"], "depth_drop")
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


class SpreadExpansionTests(TestCase):
    def test_spread_expansion_uses_only_previous_sixty_minute_median(self):
        rows = [
            minute(
                index,
                spread_p95=str(index + 1),
                book_samples=60,
                coverage="1",
            )
            for index in range(61)
        ]

        values = calculate_spread_expansion(rows)

        self.assertIsNone(values[rows[59].pk])
        self.assertEqual(values[rows[60].pk], Decimal("2.000000000000000000"))

    def test_spread_expansion_is_empty_when_prior_window_has_a_gap(self):
        rows = [
            minute(
                index,
                spread_p95="2",
                book_samples=60,
                coverage="1",
            )
            for index in range(60)
        ]
        current = minute(
            61,
            spread_p95="4",
            book_samples=60,
            coverage="1",
        )
        rows.append(current)

        values = calculate_spread_expansion(rows)

        self.assertIsNone(values[current.pk])

    def test_spread_expansion_resets_after_low_coverage_minute(self):
        rows = [
            minute(
                index,
                spread_p95="2",
                book_samples=60,
                coverage="0.70" if index == 30 else "1",
            )
            for index in range(61)
        ]

        values = calculate_spread_expansion(rows)

        self.assertIsNone(values[rows[-1].pk])

    def test_spread_expansion_is_empty_when_baseline_median_is_zero(self):
        rows = [
            minute(
                index,
                spread_p95="0" if index < 60 else "2",
                book_samples=60,
                coverage="1",
            )
            for index in range(61)
        ]

        values = calculate_spread_expansion(rows)

        self.assertIsNone(values[rows[-1].pk])

    def test_spread_research_reuses_time_split_and_training_cutpoints(self):
        for index in range(160):
            row = minute(
                index,
                spread_p95=str(100 + index),
                book_samples=60,
                coverage="1",
            )
            row.future_5m_return = Decimal(index - 80) / Decimal("100000")
            row.save(update_fields=["future_5m_return"])

        result = build_spread_expansion_research("ETHUSDT")

        self.assertEqual(result["metric"]["key"], "spread_expansion")
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


class Top5ImbalanceTests(TestCase):
    def test_top5_imbalance_uses_minute_mean_with_book_quality_gate(self):
        valid = minute(
            0,
            book_samples=60,
            coverage="1",
            imbalance_top5_mean="0.25",
            imbalance_top5_samples=60,
        )
        low_coverage = minute(
            1,
            book_samples=47,
            coverage="0.79",
            imbalance_top5_mean="0.25",
            imbalance_top5_samples=47,
        )
        partial_top5 = minute(
            2,
            book_samples=60,
            coverage="1",
            imbalance_top5_mean="0.25",
            imbalance_top5_samples=47,
        )

        self.assertEqual(
            top5_imbalance(valid),
            Decimal("0.25"),
        )
        self.assertIsNone(top5_imbalance(low_coverage))
        self.assertIsNone(top5_imbalance(partial_top5))

    def test_top5_imbalance_rejects_missing_or_out_of_range_mean(self):
        missing = minute(
            0,
            book_samples=60,
            coverage="1",
            imbalance_top5_samples=60,
        )
        out_of_range = minute(
            1,
            book_samples=60,
            coverage="1",
            imbalance_top5_mean="1.01",
            imbalance_top5_samples=60,
        )

        self.assertIsNone(top5_imbalance(missing))
        self.assertIsNone(top5_imbalance(out_of_range))

    def test_top5_research_reuses_time_split_and_training_cutpoints(self):
        for index in range(100):
            row = minute(
                index,
                book_samples=60,
                coverage="1",
                imbalance_top5_mean=str(
                    Decimal(index * 2 - 99) / Decimal(100)
                ),
                imbalance_top5_samples=60,
            )
            row.future_5m_return = Decimal(index - 50) / Decimal("100000")
            row.save(update_fields=["future_5m_return"])

        result = build_top5_imbalance_research("ETHUSDT")

        self.assertEqual(result["metric"]["key"], "top5_imbalance")
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
