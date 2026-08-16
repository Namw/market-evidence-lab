from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.microstructure.calculations import OrderBookFeatures
from apps.microstructure.models import OrderBookFiveMinuteSummary, OrderBookSnapshot
from apps.microstructure.services import (
    aggregate_interval,
    aggregate_range,
    save_snapshot,
)

START = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def features(*, update_id: int, mid: str, spread_bps: str = "1") -> OrderBookFeatures:
    mid_value = Decimal(mid)
    return OrderBookFeatures(
        symbol="ETHUSDT",
        event_time=START + timedelta(seconds=update_id),
        received_at=START + timedelta(seconds=update_id, milliseconds=10),
        update_id=update_id,
        best_bid=mid_value - Decimal("0.5"),
        best_ask=mid_value + Decimal("0.5"),
        mid_price=mid_value,
        spread=Decimal("1"),
        spread_bps=Decimal(spread_bps),
        bid_depth_top5_quote=Decimal(100 + update_id),
        ask_depth_top5_quote=Decimal(200 + update_id),
        bid_depth_top10_quote=Decimal(300 + update_id),
        ask_depth_top10_quote=Decimal(400 + update_id),
        bid_depth_top20_quote=Decimal(500 + update_id),
        ask_depth_top20_quote=Decimal(600 + update_id),
        imbalance_top5=Decimal("0.1") * update_id,
        imbalance_top10=Decimal("0.01") * update_id,
        imbalance_top20=Decimal("0.001") * update_id,
    )


class OrderBookServiceTests(TestCase):
    def save(self, second: int, mid: str, spread_bps: str = "1") -> OrderBookSnapshot:
        return save_snapshot(
            features(update_id=second + 1, mid=mid, spread_bps=spread_bps),
            sampled_at=START + timedelta(seconds=second),
        )

    def test_snapshot_save_is_idempotent_per_symbol_and_second(self):
        self.save(0, "100")
        save_snapshot(
            features(update_id=2, mid="101"),
            sampled_at=START,
        )

        self.assertEqual(OrderBookSnapshot.objects.count(), 1)
        self.assertEqual(OrderBookSnapshot.objects.get().mid_price, Decimal("101"))

    def test_five_minute_summary_uses_left_closed_right_open_interval(self):
        self.save(0, "100", "1")
        self.save(120, "105", "2")
        self.save(299, "102", "3")
        self.save(300, "999", "99")

        summary = aggregate_interval(symbol="ETHUSDT", interval_start=START)

        assert summary is not None
        self.assertEqual(summary.interval_end, START + timedelta(minutes=5))
        self.assertEqual(summary.snapshot_count, 3)
        self.assertEqual(summary.mid_open, Decimal("100"))
        self.assertEqual(summary.mid_high, Decimal("105"))
        self.assertEqual(summary.mid_low, Decimal("100"))
        self.assertEqual(summary.mid_close, Decimal("102"))
        self.assertEqual(summary.spread_bps_mean, Decimal("2"))
        self.assertEqual(summary.spread_bps_max, Decimal("3"))
        self.assertEqual(summary.spread_bps_end, Decimal("3"))
        self.assertEqual(
            summary.bid_depth_top5_quote_mean,
            Decimal("240.666666666666666667"),
        )
        self.assertEqual(
            summary.ask_depth_top20_quote_mean,
            Decimal("740.666666666666666667"),
        )
        self.assertEqual(
            summary.imbalance_top10_mean,
            Decimal("1.406666666666666667"),
        )
        self.assertEqual(summary.imbalance_top20_end, Decimal("0.3"))

    def test_reaggregation_updates_one_existing_summary(self):
        last = self.save(0, "100")
        aggregate_interval(symbol="ETHUSDT", interval_start=START)
        last.mid_price = Decimal("110")
        last.save(update_fields=["mid_price"])

        aggregate_interval(symbol="ETHUSDT", interval_start=START)

        self.assertEqual(OrderBookFiveMinuteSummary.objects.count(), 1)
        self.assertEqual(OrderBookFiveMinuteSummary.objects.get().mid_close, Decimal("110"))

    def test_empty_interval_does_not_create_a_summary(self):
        self.assertIsNone(
            aggregate_interval(symbol="ETHUSDT", interval_start=START)
        )
        self.assertFalse(OrderBookFiveMinuteSummary.objects.exists())

    def test_range_reports_written_and_empty_intervals(self):
        self.save(0, "100")

        written, empty = aggregate_range(
            symbol="ETHUSDT",
            range_start=START,
            range_end=START + timedelta(minutes=10),
        )

        self.assertEqual((written, empty), (1, 1))
