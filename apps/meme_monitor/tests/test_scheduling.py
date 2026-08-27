from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.meme_monitor.scheduling import (
    claim_due_meme_schedules,
    execute_claimed_meme_schedule,
    get_builtin_meme_schedule,
    set_meme_schedule_enabled,
)


@override_settings(MEME_MONITOR_POLL_INTERVAL_SECONDS=30)
class MemeMonitorSchedulingTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 28, 0, tzinfo=UTC)

    def test_builtin_schedule_is_disabled_by_default(self):
        schedule = get_builtin_meme_schedule()

        self.assertFalse(schedule.enabled)
        self.assertEqual(schedule.interval_seconds, 30)

    def test_enabled_due_schedule_is_claimed_once_and_advanced(self):
        schedule = set_meme_schedule_enabled(True, now=self.now)

        first_claim = claim_due_meme_schedules(now=self.now)
        second_claim = claim_due_meme_schedules(now=self.now)

        schedule.refresh_from_db()
        self.assertEqual(first_claim, [schedule.pk])
        self.assertEqual(second_claim, [])
        self.assertEqual(schedule.last_run_at, self.now)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(seconds=30))

    @patch("apps.meme_monitor.scheduling.call_command")
    def test_claimed_enabled_schedule_executes_one_monitor_cycle(self, call_command):
        schedule = set_meme_schedule_enabled(True, now=self.now)

        execute_claimed_meme_schedule(schedule.pk)

        call_command.assert_called_once_with("run_meme_monitor", "--once")

    @patch("apps.meme_monitor.scheduling.call_command")
    def test_disabled_schedule_is_not_executed_after_claim(self, call_command):
        schedule = set_meme_schedule_enabled(True, now=self.now)
        set_meme_schedule_enabled(False, now=self.now)

        execute_claimed_meme_schedule(schedule.pk)

        call_command.assert_not_called()
