from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.meme_monitor.detector import MemeAnomalyDetector, MemeDetectorConfig
from apps.meme_monitor.models import MemeAnomalyEventRecord, MemeMarketSnapshot
from apps.meme_monitor.storage import DjangoMemeMonitorStorage
from apps.meme_monitor.tests.helpers import make_snapshot


class DjangoMemeMonitorStorageTests(TestCase):
    def setUp(self):
        self.storage = DjangoMemeMonitorStorage()
        self.now = datetime(2026, 8, 27, 12, tzinfo=UTC)

    def test_saves_snapshots_history_event_and_checks_cooldown(self):
        previous = make_snapshot(
            timestamp=self.now - timedelta(seconds=30),
            volume_5m=Decimal(10000),
        )
        current = make_snapshot(timestamp=self.now)
        self.storage.save_snapshots([previous])
        records = self.storage.save_snapshots([current])

        history = self.storage.recent_volume_5m(
            chain="BSC",
            pair_address="0xpair",
            before=self.now,
            limit=10,
        )
        self.assertEqual(history, [Decimal(10000)])

        detector = MemeAnomalyDetector(
            MemeDetectorConfig(
                price_change_5m_pct=Decimal(30),
                minimum_volume_5m_usd=Decimal(5000),
                volume_spike_multiplier=Decimal(3),
                volume_history_min_samples=3,
                minimum_transactions_5m=20,
                minimum_liquidity_usd=Decimal(5000),
            )
        )
        event = detector.detect(current, historical_volumes=[], event_time=self.now)
        self.storage.save_event(event, snapshot_record=records["0xpair"])

        self.assertEqual(MemeMarketSnapshot.objects.count(), 2)
        self.assertEqual(MemeAnomalyEventRecord.objects.count(), 1)
        self.assertTrue(
            self.storage.in_cooldown(
                chain="BSC",
                token_address="0xtoken",
                anomaly_type="market_spike",
                since=self.now - timedelta(minutes=10),
            )
        )
