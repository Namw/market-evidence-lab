from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.microstructure.models import MarketMinute, MicrostructureResearchSnapshot
from apps.scheduling.models import ResearchSnapshotSchedule
from apps.scheduling.research_snapshot_workflow import (
    claim_due_research_snapshot_schedules,
    execute_claimed_research_snapshot_schedule,
    generate_research_snapshot,
    get_builtin_research_snapshot_schedule,
)


NOW = datetime(2026, 8, 26, 0, 15, tzinfo=UTC)


class ResearchSnapshotScheduleTests(TestCase):
    def test_generation_refreshes_mature_labels_and_persists_payload(self):
        start = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
        for offset in range(6):
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start + timedelta(minutes=offset),
                minute_end=start + timedelta(minutes=offset + 1),
                open_price=str(100 + offset),
                high_price=str(101 + offset),
                low_price=str(99 + offset),
                close_price=str(100 + offset),
                quote_volume="1000",
                taker_buy_quote="600",
                taker_sell_quote="400",
                kline_closed=True,
            )

        snapshot = generate_research_snapshot("ETHUSDT")

        self.assertEqual(MicrostructureResearchSnapshot.objects.count(), 1)
        self.assertEqual(snapshot.data_cutoff, start + timedelta(minutes=5))
        self.assertEqual(snapshot.labels_updated, 1)
        self.assertEqual(snapshot.labeled_count, 1)
        self.assertEqual(snapshot.payload["overview"]["symbol"], "ETHUSDT")

    def test_builtin_schedule_is_enabled_once_daily(self):
        schedule = get_builtin_research_snapshot_schedule()

        self.assertTrue(schedule.enabled)
        self.assertEqual((schedule.run_time.hour, schedule.run_time.minute), (0, 30))

    def test_due_schedule_is_claimed_once_and_advanced_to_next_day(self):
        schedule = get_builtin_research_snapshot_schedule()
        schedule.next_run_at = NOW - timedelta(minutes=1)
        schedule.save(update_fields=["next_run_at"])

        claimed = claim_due_research_snapshot_schedules(now=NOW)

        self.assertEqual(claimed, [schedule.pk])
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_run_at, NOW)
        self.assertEqual(
            schedule.next_run_at,
            datetime(2026, 8, 26, 16, 30, tzinfo=UTC),
        )
        self.assertEqual(claim_due_research_snapshot_schedules(now=NOW), [])

    @override_settings(MICROSTRUCTURE_SYMBOLS=["ETHUSDT", "ZECUSDT"])
    @patch(
        "apps.scheduling.research_snapshot_workflow.generate_research_snapshot"
    )
    def test_claimed_schedule_generates_every_configured_symbol(self, generate):
        schedule = get_builtin_research_snapshot_schedule()
        generate.side_effect = [object(), object()]

        snapshots = execute_claimed_research_snapshot_schedule(schedule.pk)

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(
            [call.args for call in generate.call_args_list],
            [("ETHUSDT",), ("ZECUSDT",)],
        )
        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.last_success_at)
        self.assertEqual(schedule.last_error, "")

    @patch(
        "apps.scheduling.research_snapshot_workflow.generate_research_snapshot",
        side_effect=RuntimeError("secret response"),
    )
    def test_failure_records_only_safe_error(self, generate):
        schedule = get_builtin_research_snapshot_schedule()

        with self.assertRaises(RuntimeError):
            execute_claimed_research_snapshot_schedule(schedule.pk)

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_error, "RuntimeError: research snapshot failed")
        self.assertNotIn("secret", schedule.last_error)
