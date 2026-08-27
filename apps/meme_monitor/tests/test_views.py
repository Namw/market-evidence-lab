from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.meme_monitor.domain import MemeAnomalyEvent
from apps.meme_monitor.models import MemeMonitorRun
from apps.meme_monitor.selectors import dashboard_context
from apps.meme_monitor.storage import DjangoMemeMonitorStorage
from apps.meme_monitor.tests.helpers import make_snapshot


@override_settings(
    MEME_MONITOR_POLL_INTERVAL_SECONDS=30,
    MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS=24,
)
class MemeMonitorViewTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 28, 0, tzinfo=UTC)
        self.storage = DjangoMemeMonitorStorage()

    def test_empty_dashboard_renders(self):
        response = self.client.get(reverse("meme_monitor:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Meme 新币观察")
        self.assertContains(response, "尚无异常事件")

    def test_dashboard_reports_live_run_and_event_outcomes(self):
        run = self.storage.start_run(
            chain="BSC",
            mode=MemeMonitorRun.Mode.CONTINUOUS,
            process_id=123,
            started_at=self.now - timedelta(minutes=20),
        )
        MemeMonitorRun.objects.filter(pk=run.pk).update(
            heartbeat_at=self.now - timedelta(seconds=10)
        )
        event_time = self.now - timedelta(minutes=20)
        event_snapshot = make_snapshot(
            timestamp=event_time,
            price_usd=Decimal(1),
        )
        record = self.storage.save_snapshots([event_snapshot])["0xpair"]
        self.storage.save_event(
            MemeAnomalyEvent(
                event_time=event_time,
                chain="BSC",
                token_address="0xtoken",
                pair_address="0xpair",
                symbol="MEME",
                name="Meme Token",
                pair_age_minutes=43,
                price_usd=Decimal(1),
                price_change_5m=Decimal(38),
                price_change_1h=Decimal(170),
                volume_5m=Decimal(42000),
                liquidity_usd=Decimal(31000),
                buys_5m=186,
                sells_5m=74,
                triggered_rules=("price_spike", "active_trading"),
            ),
            snapshot_record=record,
        )
        self.storage.save_snapshots(
            [
                make_snapshot(
                    timestamp=event_time + timedelta(minutes=5, seconds=10),
                    price_usd=Decimal("1.10"),
                ),
                make_snapshot(
                    timestamp=event_time + timedelta(minutes=15, seconds=10),
                    price_usd=Decimal("0.80"),
                ),
            ]
        )

        context = dashboard_context(now=self.now)
        self.assertEqual(context["status"]["key"], "running")
        outcomes = context["events"][0]["outcomes"]
        self.assertEqual(outcomes[0]["return_pct"], Decimal("10.00"))
        self.assertEqual(outcomes[1]["return_pct"], Decimal("-20.00"))
        self.assertEqual(outcomes[2]["status"], "pending")

    def test_running_record_with_old_heartbeat_is_stale(self):
        self.storage.start_run(
            chain="BSC",
            mode=MemeMonitorRun.Mode.CONTINUOUS,
            process_id=123,
            started_at=self.now - timedelta(minutes=10),
        )
        context = dashboard_context(now=self.now)
        self.assertEqual(context["status"]["key"], "stale")

    def test_recent_partial_cycle_marks_live_run_degraded(self):
        run = self.storage.start_run(
            chain="BSC",
            mode=MemeMonitorRun.Mode.CONTINUOUS,
            process_id=123,
            started_at=self.now - timedelta(minutes=1),
        )
        cycle = self.storage.start_cycle(
            run.pk,
            started_at=self.now - timedelta(seconds=30),
        )
        self.storage.finish_cycle(
            run.pk,
            cycle.pk,
            finished_at=self.now - timedelta(seconds=10),
            fetched_pairs=20,
            tracked_pairs=20,
            saved_snapshots=10,
            detected_anomalies=0,
            warning_message="one market batch failed",
        )

        context = dashboard_context(now=self.now)
        cycle.refresh_from_db()
        self.assertEqual(context["status"]["key"], "degraded")
        self.assertEqual(cycle.status, "partial")
        self.assertEqual(context["tracked_pair_count"], 20)
