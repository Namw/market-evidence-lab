from datetime import UTC, datetime
from decimal import Decimal

from django.test import SimpleTestCase

from apps.meme_monitor.detector import MemeAnomalyDetector, MemeDetectorConfig
from apps.meme_monitor.tests.helpers import make_snapshot


class MemeAnomalyDetectorTests(SimpleTestCase):
    def setUp(self):
        self.detector = MemeAnomalyDetector(
            MemeDetectorConfig(
                price_change_5m_pct=Decimal(30),
                minimum_volume_5m_usd=Decimal(5000),
                volume_spike_multiplier=Decimal(3),
                volume_history_min_samples=3,
                minimum_transactions_5m=20,
                minimum_liquidity_usd=Decimal(5000),
            )
        )
        self.now = datetime(2026, 8, 27, 12, tzinfo=UTC)

    def test_detects_composite_anomaly_and_marks_optional_volume_spike(self):
        event = self.detector.detect(
            make_snapshot(timestamp=self.now),
            historical_volumes=[Decimal(10000)] * 3,
            event_time=self.now,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.pair_age_minutes, 43)
        self.assertEqual(
            event.triggered_rules,
            (
                "price_spike",
                "volume_threshold",
                "volume_spike",
                "active_trading",
                "minimum_liquidity",
            ),
        )

    def test_low_activity_rejects_large_price_jump(self):
        event = self.detector.detect(
            make_snapshot(timestamp=self.now, buys_5m=2, sells_5m=1),
            historical_volumes=[],
            event_time=self.now,
        )
        self.assertIsNone(event)

    def test_insufficient_history_does_not_block_mvp_absolute_volume_rule(self):
        event = self.detector.detect(
            make_snapshot(timestamp=self.now),
            historical_volumes=[],
            event_time=self.now,
        )
        self.assertIsNotNone(event)
        self.assertNotIn("volume_spike", event.triggered_rules)
