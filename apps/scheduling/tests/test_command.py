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

    @patch(
        "apps.scheduling.management.commands.run_scheduler.execute_claimed_deribit_options_workflow"
    )
    @patch(
        "apps.scheduling.management.commands.run_scheduler.claim_due_deribit_options_schedules"
    )
    @patch("apps.scheduling.management.commands.run_scheduler.claim_due_news_schedules")
    @patch("apps.scheduling.management.commands.run_scheduler.claim_due_schedules")
    def test_once_executes_claimed_deribit_workflow(
        self,
        claim_market,
        claim_news,
        claim_deribit,
        execute_deribit,
    ):
        claim_market.return_value = []
        claim_news.return_value = []
        claim_deribit.return_value = [40]

        call_command("run_scheduler", "--once")

        execute_deribit.assert_called_once()
        self.assertEqual(execute_deribit.call_args.args, (40,))

    @patch(
        "apps.scheduling.management.commands.run_scheduler.execute_claimed_news_workflow"
    )
    @patch("apps.scheduling.management.commands.run_scheduler.claim_due_news_schedules")
    @patch("apps.scheduling.management.commands.run_scheduler.claim_due_schedules")
    def test_once_executes_claimed_news_workflow_in_same_executor(
        self,
        claim_market,
        claim_news,
        execute_news,
    ):
        claim_market.return_value = []
        claim_news.return_value = [30]

        call_command("run_scheduler", "--once")

        execute_news.assert_called_once()
        self.assertEqual(execute_news.call_args.args, (30,))
