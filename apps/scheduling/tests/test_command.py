from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.scheduling.models import SchedulerHeartbeat


class SchedulerCommandTests(TestCase):
    @patch("apps.scheduling.management.commands.run_scheduler.claim_due_schedules")
    def test_once_checks_once_and_exits(self, claim):
        claim.return_value = []
        stdout = StringIO()

        call_command("run_scheduler", "--once", stdout=stdout)

        claim.assert_called_once_with()
        self.assertIn("Scheduler check complete", stdout.getvalue())
        self.assertFalse(SchedulerHeartbeat.objects.get().is_running)

    @patch("apps.scheduling.management.commands.run_scheduler.execute_claimed_workflow")
    @patch("apps.scheduling.management.commands.run_scheduler.claim_due_schedules")
    def test_one_task_exception_does_not_stop_later_claimed_task(self, claim, execute):
        claim.return_value = [10, 20]
        execute.side_effect = [RuntimeError("boom"), object()]
        stderr = StringIO()

        call_command("run_scheduler", "--once", stderr=stderr)

        self.assertEqual(execute.call_count, 2)
        self.assertIn("continuing", stderr.getvalue())

    def test_poll_interval_is_validated(self):
        with self.assertRaisesMessage(Exception, "between 1 and 3600"):
            call_command("run_scheduler", "--once", "--poll-interval", "0")
