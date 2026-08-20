from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.microstructure.calculations import MinuteKline, OrderBookFeatures
from apps.microstructure.models import MarketMinute
from apps.microstructure.services import save_book_sample, save_kline

START = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def kline(
    *,
    close: str = "101",
    buy: str = "600",
    total: str = "1000",
    minute_offset: int = 0,
    closed: bool = False,
) -> MinuteKline:
    buy_value = Decimal(buy)
    total_value = Decimal(total)
    sell_value = total_value - buy_value
    return MinuteKline(
        symbol="ETHUSDT",
        event_time=START + timedelta(minutes=minute_offset, seconds=30),
        minute_start=START + timedelta(minutes=minute_offset),
        minute_end=START + timedelta(minutes=minute_offset + 1),
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal(close),
        quote_volume=total_value,
        taker_buy_quote=buy_value,
        taker_sell_quote=sell_value,
        delta_quote=buy_value - sell_value,
        trade_count=20,
        first_trade_id=10,
        last_trade_id=29,
        closed=closed,
    )


def book(
    *,
    bid_depth: str,
    ask_depth: str,
    spread: str,
    top5_imbalance: str | None = "0",
) -> OrderBookFeatures:
    return OrderBookFeatures(
        symbol="ETHUSDT",
        event_time=START,
        received_at=START,
        update_id=1,
        best_bid=Decimal("100"),
        best_ask=Decimal("100.1"),
        mid_price=Decimal("100.05"),
        spread=Decimal("0.1"),
        spread_bps=Decimal(spread),
        bid_depth_top5_quote=Decimal(1),
        ask_depth_top5_quote=Decimal(1),
        bid_depth_top10_quote=Decimal(1),
        ask_depth_top10_quote=Decimal(1),
        bid_depth_top20_quote=Decimal(bid_depth),
        ask_depth_top20_quote=Decimal(ask_depth),
        imbalance_top5=(
            Decimal(top5_imbalance) if top5_imbalance is not None else None
        ),
        imbalance_top10=Decimal(0),
        imbalance_top20=Decimal(0),
    )


class MarketMinuteServiceTests(TestCase):
    def test_exchange_kline_updates_one_minute_idempotently(self):
        save_kline(kline())
        save_kline(kline(close="103", buy="700"))

        self.assertEqual(MarketMinute.objects.count(), 1)
        row = MarketMinute.objects.get()
        self.assertEqual(row.close_price, Decimal("103"))
        self.assertEqual(row.taker_buy_quote, Decimal("700"))
        self.assertEqual(row.taker_sell_quote, Decimal("300"))
        self.assertEqual(row.delta_quote, Decimal("400"))

    def test_closed_klines_automatically_label_the_minute_five_minutes_ago(self):
        for offset in range(6):
            save_kline(
                kline(
                    close=str(100 + offset),
                    minute_offset=offset,
                    closed=True,
                )
            )

        first = MarketMinute.objects.get(minute_start=START)
        second = MarketMinute.objects.get(
            minute_start=START + timedelta(minutes=1)
        )
        self.assertEqual(
            first.future_5m_return,
            Decimal("0.050000000000000000"),
        )
        self.assertIsNone(second.future_5m_return)

    def test_second_book_samples_build_depth_mean_p95_and_coverage(self):
        save_book_sample(
            book(
                bid_depth="100",
                ask_depth="200",
                spread="1",
                top5_imbalance="0.6",
            ),
            sampled_at=START,
        )
        save_book_sample(
            book(
                bid_depth="300",
                ask_depth="400",
                spread="3",
                top5_imbalance="-0.2",
            ),
            sampled_at=START + timedelta(seconds=1),
        )

        row = MarketMinute.objects.get()
        self.assertEqual(row.bid_depth_open, Decimal("100"))
        self.assertEqual(row.bid_depth_close, Decimal("300"))
        self.assertEqual(row.bid_depth_mean, Decimal("200"))
        self.assertEqual(row.ask_depth_mean, Decimal("300"))
        self.assertEqual(row.spread_bps_mean, Decimal("2"))
        self.assertEqual(row.spread_bps_p95, Decimal("3"))
        self.assertEqual(row.imbalance_top5_close, Decimal("-0.2"))
        self.assertEqual(row.imbalance_top5_mean, Decimal("0.2"))
        self.assertEqual(row.imbalance_top5_sample_count, 2)
        self.assertEqual(row.book_sample_count, 2)
        self.assertAlmostEqual(float(row.coverage_ratio), 2 / 60, places=6)

    def test_duplicate_second_does_not_double_count_depth(self):
        save_book_sample(book(bid_depth="100", ask_depth="200", spread="1"), sampled_at=START)
        _, written = save_book_sample(book(bid_depth="999", ask_depth="999", spread="9"), sampled_at=START)

        self.assertFalse(written)
        row = MarketMinute.objects.get()
        self.assertEqual(row.book_sample_count, 1)
        self.assertEqual(row.bid_depth_close, Decimal("100"))
        self.assertEqual(row.imbalance_top5_sample_count, 1)

    def test_invalid_top5_imbalance_does_not_pollute_minute_mean(self):
        save_book_sample(
            book(
                bid_depth="100",
                ask_depth="200",
                spread="1",
                top5_imbalance=None,
            ),
            sampled_at=START,
        )

        row = MarketMinute.objects.get()
        self.assertEqual(row.book_sample_count, 1)
        self.assertEqual(row.imbalance_top5_sample_count, 0)
        self.assertIsNone(row.imbalance_top5_mean)
        self.assertIsNone(row.imbalance_top5_close)
