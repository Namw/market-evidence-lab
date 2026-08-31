from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.meme_monitor.domain import MemeAnomalyEvent
from apps.meme_monitor.models import MemeContinuationResearchEpisode
from apps.meme_monitor.research import (
    MemeContinuationResearchConfig,
    MemeContinuationResearchService,
)
from apps.meme_monitor.storage import DjangoMemeMonitorStorage
from apps.meme_monitor.tests.helpers import make_snapshot


class MemeContinuationResearchServiceTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 31, 12, tzinfo=UTC)
        self.storage = DjangoMemeMonitorStorage()
        self.research = MemeContinuationResearchService(
            MemeContinuationResearchConfig(
                rule_version="launchpad_5m_v1",
                entry_delay_seconds=30,
                horizon_seconds=300,
                observation_tolerance_seconds=90,
                notional_usd=Decimal(100),
                fee_bps_per_side=Decimal(30),
                max_price_impact_pct=Decimal(5),
            )
        )

    def test_first_anomaly_tracks_executable_entry_and_migrated_pool_exit(self):
        launchpad = make_snapshot(
            timestamp=self.now,
            pair_created_at=self.now - timedelta(minutes=2),
            price_usd=Decimal(1),
            liquidity_usd=Decimal(10_000),
            dex="four-meme",
            launchpad_graduation_percentage=Decimal(80),
            launchpad_completed=False,
        )
        self.research.observe_launchpads([launchpad])
        event = self._save_event(launchpad)

        episode = self.research.open_first_episode(event)
        duplicate = self.research.open_first_episode(event)

        self.assertEqual(episode.pk, duplicate.pk)
        self.assertEqual(MemeContinuationResearchEpisode.objects.count(), 1)

        entry = make_snapshot(
            timestamp=self.now + timedelta(seconds=30),
            pair_created_at=launchpad.pair_created_at,
            price_usd=Decimal(1),
            liquidity_usd=Decimal(10_000),
            dex="four-meme",
            launchpad_completed=False,
        )
        self.research.advance([entry], observed_at=entry.timestamp)
        episode.refresh_from_db()
        self.assertEqual(episode.status, "waiting_exit")
        self.assertEqual(episode.entry_pair_address, "0xpair")
        self.assertEqual(
            episode.exit_target_at,
            self.now + timedelta(minutes=5, seconds=30),
        )

        destination_address = "0xmigrated"
        migrated_launchpad = make_snapshot(
            timestamp=self.now + timedelta(minutes=1),
            pair_created_at=launchpad.pair_created_at,
            price_usd=Decimal("0.95"),
            liquidity_usd=Decimal(8_000),
            dex="four-meme",
            launchpad_completed=True,
            launchpad_completed_at=self.now + timedelta(seconds=50),
            migrated_destination_pair_address=destination_address,
        )
        destination = make_snapshot(
            timestamp=migrated_launchpad.timestamp,
            pair_created_at=migrated_launchpad.timestamp,
            pair_address=destination_address,
            price_usd=Decimal("1.02"),
            liquidity_usd=Decimal(20_000),
            dex="pancakeswap_v2",
            launchpad_completed=None,
        )
        self.research.advance(
            [migrated_launchpad, destination],
            observed_at=destination.timestamp,
        )
        episode.refresh_from_db()
        self.assertEqual(episode.current_pair_address, destination_address)
        self.assertEqual(
            episode.migrated_destination_pair_address,
            destination_address,
        )
        self.assertIn(
            destination_address,
            self.research.tracked_pair_addresses(chain="BSC"),
        )

        exit_snapshot = make_snapshot(
            timestamp=episode.exit_target_at,
            pair_created_at=destination.pair_created_at,
            pair_address=destination_address,
            price_usd=Decimal("1.20"),
            liquidity_usd=Decimal(20_000),
            dex="pancakeswap_v2",
            launchpad_completed=None,
        )
        self.research.advance([exit_snapshot], observed_at=exit_snapshot.timestamp)
        episode.refresh_from_db()

        self.assertEqual(episode.status, "completed")
        self.assertEqual(episode.exit_pair_address, destination_address)
        self.assertEqual(episode.gross_return_pct, Decimal("20"))
        self.assertLess(episode.net_return_pct, episode.gross_return_pct)
        response = self.client.get(reverse("meme_monitor:research"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "跨池迁移校正后的可执行样本")
        self.assertContains(response, "研究完成")
        self.assertContains(response, "20.00%")

    def test_non_launchpad_anomaly_does_not_open_research_episode(self):
        snapshot = make_snapshot(timestamp=self.now, launchpad_completed=None)
        self.research.observe_launchpads([snapshot])
        event = self._save_event(snapshot)

        self.assertIsNone(self.research.open_first_episode(event))
        self.assertFalse(MemeContinuationResearchEpisode.objects.exists())

    def test_entry_expires_when_price_impact_never_becomes_executable(self):
        launchpad = make_snapshot(
            timestamp=self.now,
            liquidity_usd=Decimal(1_000),
            launchpad_completed=False,
        )
        self.research.observe_launchpads([launchpad])
        episode = self.research.open_first_episode(self._save_event(launchpad))
        late = make_snapshot(
            timestamp=self.now + timedelta(minutes=3),
            liquidity_usd=Decimal(1_000),
            launchpad_completed=False,
        )

        self.research.advance([late], observed_at=late.timestamp)
        episode.refresh_from_db()

        self.assertEqual(episode.status, "unavailable")
        self.assertEqual(
            episode.failure_reason,
            "entry_not_executable_within_tolerance",
        )

    def _save_event(self, snapshot):
        return self.storage.save_event(
            MemeAnomalyEvent(
                event_time=snapshot.timestamp,
                chain=snapshot.chain,
                token_address=snapshot.token_address,
                pair_address=snapshot.pair_address,
                symbol=snapshot.symbol,
                name=snapshot.name,
                pair_age_minutes=snapshot.pair_age_minutes(),
                price_usd=snapshot.price_usd,
                price_change_5m=snapshot.price_change_5m,
                price_change_1h=snapshot.price_change_1h,
                volume_5m=snapshot.volume_5m,
                liquidity_usd=snapshot.liquidity_usd,
                buys_5m=snapshot.buys_5m,
                sells_5m=snapshot.sells_5m,
                triggered_rules=(
                    "price_spike",
                    "volume_threshold",
                    "active_trading",
                    "minimum_liquidity",
                ),
            ),
        )
