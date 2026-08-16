from datetime import UTC, datetime
from decimal import Decimal

from django.test import SimpleTestCase

from apps.microstructure.calculations import (
    DepthPayloadError,
    depth_imbalance,
    floor_time,
    parse_depth_message,
)

NOW = datetime(2026, 8, 17, 12, 0, 0, 500_000, tzinfo=UTC)


def depth_payload() -> dict[str, object]:
    return {
        "e": "depthUpdate",
        "E": 1_776_600_000_123,
        "T": 1_776_600_000_120,
        "s": "ETHUSDT",
        "U": 100,
        "u": 120,
        "pu": 99,
        "b": [[str(100 - index), "1"] for index in range(20)],
        "a": [[str(101 + index), "1"] for index in range(20)],
    }


class OrderBookCalculationTests(SimpleTestCase):
    def test_partial_depth_payload_builds_top5_10_20_features(self):
        result = parse_depth_message(depth_payload(), received_at=NOW)

        self.assertEqual(result.symbol, "ETHUSDT")
        self.assertEqual(result.update_id, 120)
        self.assertEqual(result.received_at, NOW)
        self.assertEqual(result.best_bid, Decimal("100.000000000000000000"))
        self.assertEqual(result.best_ask, Decimal("101.000000000000000000"))
        self.assertEqual(result.mid_price, Decimal("100.500000000000000000"))
        self.assertEqual(result.spread, Decimal("1.000000000000000000"))
        self.assertEqual(result.bid_depth_top5_quote, Decimal("490.000000000000000000"))
        self.assertEqual(result.ask_depth_top5_quote, Decimal("515.000000000000000000"))
        self.assertEqual(result.bid_depth_top10_quote, Decimal("955.000000000000000000"))
        self.assertEqual(result.ask_depth_top10_quote, Decimal("1055.000000000000000000"))
        self.assertEqual(result.bid_depth_top20_quote, Decimal("1810.000000000000000000"))
        self.assertEqual(result.ask_depth_top20_quote, Decimal("2210.000000000000000000"))
        self.assertEqual(
            result.imbalance_top5,
            depth_imbalance(Decimal("490"), Decimal("515")),
        )

    def test_combined_stream_wrapper_is_accepted(self):
        result = parse_depth_message(
            {"stream": "ethusdt@depth20@500ms", "data": depth_payload()},
            received_at=NOW,
        )

        self.assertEqual(result.symbol, "ETHUSDT")

    def test_empty_depth_is_rejected(self):
        payload = depth_payload()
        payload["b"] = []

        with self.assertRaises(DepthPayloadError):
            parse_depth_message(payload, received_at=NOW)

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(DepthPayloadError):
            parse_depth_message([], received_at=NOW)

    def test_zero_depth_imbalance_is_explicitly_none(self):
        self.assertIsNone(depth_imbalance(Decimal(0), Decimal(0)))

    def test_time_floor_uses_utc_boundaries(self):
        value = datetime.fromisoformat("2026-08-17T20:07:59+08:00")

        self.assertEqual(
            floor_time(value, seconds=300),
            datetime(2026, 8, 17, 12, 5, tzinfo=UTC),
        )
