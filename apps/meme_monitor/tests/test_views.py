from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.meme_monitor.domain import MemeAnomalyEvent
from apps.meme_monitor.models import MemeMonitorRun
from apps.meme_monitor.scheduling import set_meme_schedule_enabled
from apps.meme_monitor.selectors import (
    anomalies_context,
    overview_context,
    pairs_context,
)
from apps.meme_monitor.storage import DjangoMemeMonitorStorage
from apps.meme_monitor.tests.helpers import make_snapshot
from apps.scheduling.services import record_heartbeat


@override_settings(
    MEME_MONITOR_POLL_INTERVAL_SECONDS=30,
    MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS=24,
)
class MemeMonitorViewTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 28, 0, tzinfo=UTC)
        self.storage = DjangoMemeMonitorStorage()

    def save_event_for_snapshot(self, snapshot, snapshot_record):
        self.storage.save_event(
            MemeAnomalyEvent(
                event_time=snapshot.timestamp,
                chain=snapshot.chain,
                token_address=snapshot.token_address,
                pair_address=snapshot.pair_address,
                symbol=snapshot.symbol,
                name=snapshot.name,
                pair_age_minutes=2,
                price_usd=snapshot.price_usd,
                price_change_5m=snapshot.price_change_5m,
                price_change_1h=snapshot.price_change_1h,
                volume_5m=snapshot.volume_5m,
                liquidity_usd=snapshot.liquidity_usd,
                buys_5m=snapshot.buys_5m,
                sells_5m=snapshot.sells_5m,
                triggered_rules=("price_spike", "active_trading"),
            ),
            snapshot_record=snapshot_record,
        )

    def test_empty_overview_renders_only_summary_and_cycles(self):
        response = self.client.get(reverse("meme_monitor:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Meme 新币观察")
        self.assertContains(response, "定时检查已关闭")
        self.assertContains(response, "启动定时检查")
        self.assertContains(response, "最近执行轮次")
        self.assertNotContains(response, 'id="meme-events-title"')
        self.assertNotContains(response, 'id="meme-pairs-title"')

    def test_schedule_switch_enables_and_disables_with_prg(self):
        toggle_url = reverse("meme_monitor:toggle_schedule")

        response = self.client.post(toggle_url, {"enabled": "1"})

        self.assertRedirects(response, reverse("meme_monitor:index"))
        enabled_response = self.client.get(reverse("meme_monitor:index"))
        self.assertTrue(enabled_response.context["schedule"].enabled)
        self.assertContains(enabled_response, "已启用 · 等待执行器")
        self.assertContains(enabled_response, "关闭定时检查")

        response = self.client.post(toggle_url, {"enabled": "0"})

        self.assertRedirects(response, reverse("meme_monitor:index"))
        disabled_response = self.client.get(reverse("meme_monitor:index"))
        self.assertFalse(disabled_response.context["schedule"].enabled)
        self.assertContains(disabled_response, "定时检查已关闭")

    def test_schedule_switch_rejects_invalid_state_and_requires_csrf(self):
        toggle_url = reverse("meme_monitor:toggle_schedule")

        self.assertEqual(
            self.client.post(toggle_url, {"enabled": "invalid"}).status_code,
            400,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            csrf_client.post(toggle_url, {"enabled": "1"}).status_code,
            403,
        )

    def test_enabled_schedule_reports_running_when_executor_is_online(self):
        set_meme_schedule_enabled(True, now=self.now)
        record_heartbeat(
            "test-meme-executor",
            poll_interval_seconds=30,
            now=self.now,
        )

        context = overview_context(now=self.now)

        self.assertEqual(context["status"]["key"], "running")
        self.assertEqual(context["status"]["label"], "定时检查运行中")

    def test_empty_anomalies_page_renders_only_anomaly_content(self):
        response = self.client.get(reverse("meme_monitor:anomalies"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "异常事件与后续表现")
        self.assertContains(response, "尚无异常事件")
        self.assertNotContains(response, 'id="meme-runs-title"')
        self.assertNotContains(response, 'id="meme-pairs-title"')

    def test_empty_pairs_page_renders_only_pair_content(self):
        response = self.client.get(reverse("meme_monitor:pairs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "最新跟踪 Pair")
        self.assertContains(response, "尚无新 Pair 快照")
        self.assertNotContains(response, 'id="meme-runs-title"')
        self.assertNotContains(response, 'id="meme-events-title"')

    def test_pairs_are_sorted_newest_first_and_paginated_by_30(self):
        observed_at = timezone.now()
        self.storage.save_snapshots(
            [
                make_snapshot(
                    timestamp=observed_at,
                    pair_created_at=observed_at - timedelta(minutes=index),
                    token_address=f"0xtoken{index:02}",
                    pair_address=f"0xpair{index:02}",
                    symbol=f"TOKEN{index:02}",
                )
                for index in range(35)
            ]
        )

        first_page = pairs_context(now=observed_at, page_number=1)
        second_page = pairs_context(now=observed_at, page_number=2)

        self.assertEqual(first_page["page"].paginator.per_page, 30)
        self.assertEqual(first_page["page"].paginator.count, 35)
        self.assertEqual(len(first_page["latest_pairs"]), 30)
        self.assertEqual(first_page["latest_pairs"][0]["snapshot"].symbol, "TOKEN00")
        self.assertEqual(first_page["latest_pairs"][-1]["snapshot"].symbol, "TOKEN29")
        self.assertEqual(len(second_page["latest_pairs"]), 5)
        self.assertEqual(second_page["latest_pairs"][0]["snapshot"].symbol, "TOKEN30")

        response = self.client.get(reverse("meme_monitor:pairs"), {"page": 2})
        self.assertContains(response, "第 2 / 2 页")
        self.assertContains(response, "第 31–35 条，共 35 个 Pair")
        self.assertContains(response, '?page=1">上一页</a>')

    def test_anomalies_are_sorted_newest_first_and_paginated_by_30(self):
        observed_at = timezone.now()
        snapshots = [
            make_snapshot(
                timestamp=observed_at - timedelta(minutes=index),
                pair_created_at=observed_at - timedelta(minutes=index + 2),
                token_address=f"0xeventtoken{index:02}",
                pair_address=f"0xeventpair{index:02}",
                symbol=f"EVENT{index:02}",
            )
            for index in range(35)
        ]
        records = self.storage.save_snapshots(snapshots)
        for index, snapshot in enumerate(snapshots):
            self.save_event_for_snapshot(
                snapshot,
                records[f"0xeventpair{index:02}"],
            )

        first_page = anomalies_context(now=observed_at, page_number=1)
        second_page = anomalies_context(now=observed_at, page_number=2)

        self.assertEqual(first_page["page"].paginator.per_page, 30)
        self.assertEqual(first_page["page"].paginator.count, 35)
        self.assertEqual(len(first_page["events"]), 30)
        self.assertEqual(first_page["events"][0]["event"].symbol, "EVENT00")
        self.assertEqual(first_page["events"][-1]["event"].symbol, "EVENT29")
        self.assertEqual(len(second_page["events"]), 5)
        self.assertEqual(second_page["events"][0]["event"].symbol, "EVENT30")

        response = self.client.get(reverse("meme_monitor:anomalies"), {"page": 2})
        self.assertContains(response, "第 2 / 2 页")
        self.assertContains(response, "第 31–35 条，共 35 个事件")
        self.assertContains(response, '?page=1">上一页</a>')

    def test_anomalies_in_same_cycle_prioritize_stronger_price_change(self):
        observed_at = timezone.now()
        snapshots = [
            make_snapshot(
                timestamp=observed_at,
                pair_created_at=observed_at - timedelta(minutes=2),
                token_address=f"0x{symbol.lower()}",
                pair_address=f"0xpair{symbol.lower()}",
                symbol=symbol,
                price_change_5m=price_change,
            )
            for symbol, price_change in (
                ("LOW", Decimal(40)),
                ("HIGH", Decimal(120)),
            )
        ]
        records = self.storage.save_snapshots(snapshots)
        for snapshot in snapshots:
            self.save_event_for_snapshot(
                snapshot,
                records[snapshot.pair_address],
            )

        context = anomalies_context(now=observed_at)

        self.assertEqual(context["events"][0]["event"].symbol, "HIGH")
        self.assertEqual(context["events"][1]["event"].symbol, "LOW")

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

        context = overview_context(now=self.now)
        self.assertEqual(context["latest_run_status"]["key"], "running")
        outcomes = anomalies_context(now=self.now)["events"][0]["outcomes"]
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
        context = overview_context(now=self.now)
        self.assertEqual(context["latest_run_status"]["key"], "stale")

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

        context = overview_context(now=self.now)
        cycle.refresh_from_db()
        self.assertEqual(context["latest_run_status"]["key"], "degraded")
        self.assertEqual(cycle.status, "partial")
        self.assertEqual(context["tracked_pair_count"], 20)
