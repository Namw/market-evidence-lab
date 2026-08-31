from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.meme_monitor.detector import MemeAnomalyDetector, MemeDetectorConfig
from apps.meme_monitor.models import (
    MemeAnomalyEventRecord,
    MemeContinuationResearchEpisode,
    MemeMarketSnapshot,
    MemePairState,
    MemeMonitorCycle,
    MemeMonitorRun,
)
from apps.meme_monitor.service import MemeMonitorConfig, MemeMonitorService
from apps.meme_monitor.research import (
    MemeContinuationResearchConfig,
    MemeContinuationResearchService,
)
from apps.meme_monitor.storage import DjangoMemeMonitorStorage
from apps.meme_monitor.tests.helpers import make_snapshot


class MemeMonitorServiceTests(TestCase):
    def test_pipeline_saves_snapshots_and_cooldown_suppresses_repeat_event(self):
        started_at = datetime(2026, 8, 27, 12, tzinfo=UTC)
        source = FakeDataSource(make_snapshot(timestamp=started_at))
        storage = DjangoMemeMonitorStorage()
        run = storage.start_run(
            chain="BSC",
            mode=MemeMonitorRun.Mode.CONTINUOUS,
            process_id=123,
            started_at=started_at,
        )
        service = MemeMonitorService(
            data_source=source,
            storage=storage,
            detector=MemeAnomalyDetector(
                MemeDetectorConfig(
                    price_change_5m_pct=Decimal(30),
                    minimum_volume_5m_usd=Decimal(5000),
                    volume_spike_multiplier=Decimal(3),
                    volume_history_min_samples=3,
                    minimum_transactions_5m=20,
                    minimum_liquidity_usd=Decimal(5000),
                )
            ),
            config=MemeMonitorConfig(
                chain="BSC",
                new_pair_max_age_hours=24,
                poll_interval_seconds=30,
                cooldown_seconds=600,
                bootstrap_discovery_pages=1,
                max_tracked_pairs=20,
                volume_history_samples=10,
            ),
            monitor_run_id=run.pk,
        )

        first = service.run_once(observed_at=started_at)
        second = service.run_once(observed_at=started_at + timedelta(seconds=30))

        self.assertEqual(first.detected_anomalies, 1)
        self.assertEqual(second.detected_anomalies, 0)
        self.assertEqual(MemeMarketSnapshot.objects.count(), 0)
        self.assertEqual(MemePairState.objects.count(), 1)
        self.assertEqual(MemeAnomalyEventRecord.objects.count(), 1)
        run.refresh_from_db()
        self.assertEqual(run.cycle_count, 2)
        self.assertEqual(run.successful_cycle_count, 2)
        self.assertEqual(run.failed_cycle_count, 0)
        self.assertEqual(MemeMonitorCycle.objects.count(), 2)

    def test_pipeline_opens_first_launchpad_episode_and_records_entry(self):
        started_at = datetime(2026, 8, 27, 12, tzinfo=UTC)
        source = FakeDataSource(
            make_snapshot(
                timestamp=started_at,
                launchpad_graduation_percentage=Decimal(75),
                launchpad_completed=False,
            )
        )
        service = MemeMonitorService(
            data_source=source,
            storage=DjangoMemeMonitorStorage(),
            detector=MemeAnomalyDetector(
                MemeDetectorConfig(
                    price_change_5m_pct=Decimal(30),
                    minimum_volume_5m_usd=Decimal(5000),
                    volume_spike_multiplier=Decimal(3),
                    volume_history_min_samples=3,
                    minimum_transactions_5m=20,
                    minimum_liquidity_usd=Decimal(5000),
                )
            ),
            config=MemeMonitorConfig(
                chain="BSC",
                new_pair_max_age_hours=24,
                poll_interval_seconds=30,
                cooldown_seconds=600,
                bootstrap_discovery_pages=1,
                max_tracked_pairs=20,
                volume_history_samples=10,
            ),
            research=MemeContinuationResearchService(
                MemeContinuationResearchConfig(
                    rule_version="launchpad_5m_v1",
                    entry_delay_seconds=30,
                    horizon_seconds=300,
                    observation_tolerance_seconds=90,
                    notional_usd=Decimal(100),
                    fee_bps_per_side=Decimal(30),
                    max_price_impact_pct=Decimal(5),
                )
            ),
        )

        service.run_once(observed_at=started_at)
        service.run_once(observed_at=started_at + timedelta(seconds=30))

        episode = MemeContinuationResearchEpisode.objects.get()
        self.assertEqual(episode.status, "waiting_exit")
        self.assertEqual(
            episode.entry_observed_at,
            started_at + timedelta(seconds=30),
        )


class FakeDataSource:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.discovery_calls = 0

    def discover_new_pairs(self, *, observed_at, max_age_hours, max_pages):
        self.discovery_calls += 1
        if self.discovery_calls == 1:
            return [replace(self.snapshot, timestamp=observed_at)]
        return []

    def fetch_market_snapshots(self, pair_addresses, *, observed_at):
        return [replace(self.snapshot, timestamp=observed_at)]

    def close(self):
        return None
