from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.scheduling.market_pilot_workflow import (
    claim_due_market_pilot_schedules,
    execute_claimed_market_pilot_workflow,
    get_builtin_market_pilot_schedule,
)
from apps.scheduling.models import MarketPilotSchedule


NOW = datetime(2026, 8, 26, 0, 15, tzinfo=UTC)


class MarketPilotScheduleTests(TestCase):
    def test_builtin_schedule_has_four_hour_cadence_and_two_percent_threshold(self):
        schedule = get_builtin_market_pilot_schedule()

        self.assertFalse(schedule.enabled)
        self.assertEqual(schedule.interval_hours, 4)
        self.assertEqual(schedule.threshold_pct, Decimal("2"))
        self.assertEqual((schedule.run_time.hour, schedule.run_time.minute), (0, 10))

    def test_due_schedule_is_claimed_once_and_advanced(self):
        schedule = get_builtin_market_pilot_schedule()
        schedule.enabled = True
        schedule.next_run_at = NOW - timedelta(minutes=1)
        schedule.save()

        claimed = claim_due_market_pilot_schedules(now=NOW)

        self.assertEqual(claimed, [schedule.id])
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_run_at, NOW)
        self.assertEqual(schedule.next_run_at, datetime(2026, 8, 26, 4, 10, tzinfo=UTC))
        self.assertEqual(claim_due_market_pilot_schedules(now=NOW), [])

    @patch("apps.scheduling.market_pilot_workflow.monitor_market_windows")
    def test_claimed_schedule_passes_threshold_to_monitor(self, monitor):
        schedule = MarketPilotSchedule.objects.create(
            name="test",
            enabled=True,
            run_time=datetime.min.time(),
            threshold_pct=Decimal("2.5"),
            next_run_at=NOW,
        )

        execute_claimed_market_pilot_workflow(schedule.id)

        self.assertEqual(monitor.call_args.kwargs["threshold_pct"], Decimal("2.5"))

    def test_schedule_page_saves_market_pilot_configuration(self):
        response = self.client.post(
            "/system/schedules/",
            {
                "action": "save_market_pilot",
                "enabled": "on",
                "run_time": "00:10",
                "threshold_pct": "2.5",
            },
        )

        self.assertRedirects(response, "/system/schedules/")
        schedule = MarketPilotSchedule.objects.get()
        self.assertTrue(schedule.enabled)
        self.assertEqual(schedule.threshold_pct, Decimal("2.5"))
