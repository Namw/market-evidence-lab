from datetime import UTC, datetime, time, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.collection.models import CollectionRun
from apps.scheduling.deribit_workflow import (
    claim_due_deribit_options_schedules,
    execute_manual_deribit_options_workflow,
    execute_deribit_options_workflow,
    get_builtin_deribit_options_schedule,
)
from apps.scheduling.models import DeribitOptionsWorkflowRun
from apps.scheduling.services import calculate_next_run_at


NOW = datetime(2026, 8, 5, 10, 2, 30, tzinfo=UTC)


def collection_run(data_type, status=CollectionRun.Status.SUCCESS):
    return CollectionRun.objects.create(
        data_type=data_type,
        exchange=CollectionRun.Exchange.DERIBIT,
        market_type=CollectionRun.MarketType.OPTIONS,
        symbol="ETH",
        interval=CollectionRun.Interval.FIVE_MINUTES,
        range_start=NOW,
        range_end=NOW + timedelta(minutes=5),
        trigger=CollectionRun.Trigger.SCHEDULED,
        status=status,
        started_at=NOW,
        finished_at=NOW,
    )


class DeribitOptionsSchedulingTests(TestCase):
    def test_next_run_uses_daily_schedule_time(self):
        self.assertEqual(
            calculate_next_run_at(time(8, 20), after=NOW),
            datetime(2026, 8, 6, 0, 20, tzinfo=UTC),
        )

    def test_due_schedule_is_claimed_once_and_advanced(self):
        schedule = get_builtin_deribit_options_schedule()
        schedule.enabled = True
        schedule.next_run_at = NOW - timedelta(minutes=1)
        schedule.save(update_fields=["enabled", "next_run_at"])

        claimed = claim_due_deribit_options_schedules(now=NOW)
        second = claim_due_deribit_options_schedules(now=NOW)

        self.assertEqual(len(claimed), 1)
        self.assertEqual(second, [])
        run = DeribitOptionsWorkflowRun.objects.get(pk=claimed[0])
        self.assertEqual(run.observed_at, datetime(2026, 8, 5, 10, 0, tzinfo=UTC))
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, datetime(2026, 8, 6, 0, 20, tzinfo=UTC))

    @patch("apps.scheduling.deribit_workflow.collect_deribit_option_snapshot")
    @patch("apps.scheduling.deribit_workflow.collect_deribit_option_instruments")
    @patch("apps.scheduling.deribit_workflow.collect_deribit_dvol")
    def test_workflow_runs_metadata_before_snapshot(self, dvol, instruments, snapshot):
        dvol.return_value = collection_run(CollectionRun.DataType.DERIBIT_DVOL)
        instruments.return_value = collection_run(
            CollectionRun.DataType.DERIBIT_OPTION_INSTRUMENT
        )
        snapshot.return_value = collection_run(
            CollectionRun.DataType.DERIBIT_OPTION_SNAPSHOT
        )
        schedule = get_builtin_deribit_options_schedule()
        run = DeribitOptionsWorkflowRun.objects.create(
            schedule=schedule,
            trigger=DeribitOptionsWorkflowRun.Trigger.MANUAL,
            observed_at=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
            dvol_lookback_days=3,
            started_at=NOW,
        )

        result = execute_deribit_options_workflow(run)

        self.assertEqual(result.status, DeribitOptionsWorkflowRun.Status.SUCCESS)
        self.assertEqual(
            result.details["snapshot_run_id"], snapshot.return_value.pk
        )
        instruments.assert_called_once()
        snapshot.assert_called_once()

    @patch("apps.scheduling.deribit_workflow.collect_deribit_option_snapshot")
    @patch("apps.scheduling.deribit_workflow.collect_deribit_option_instruments")
    @patch("apps.scheduling.deribit_workflow.collect_deribit_dvol")
    def test_failed_metadata_prevents_incomplete_snapshot(
        self, dvol, instruments, snapshot
    ):
        dvol.return_value = collection_run(CollectionRun.DataType.DERIBIT_DVOL)
        instruments.return_value = collection_run(
            CollectionRun.DataType.DERIBIT_OPTION_INSTRUMENT,
            CollectionRun.Status.FAILED,
        )
        schedule = get_builtin_deribit_options_schedule()
        run = DeribitOptionsWorkflowRun.objects.create(
            schedule=schedule,
            trigger=DeribitOptionsWorkflowRun.Trigger.MANUAL,
            observed_at=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
            dvol_lookback_days=3,
            started_at=NOW,
        )

        result = execute_deribit_options_workflow(run)

        self.assertEqual(result.status, DeribitOptionsWorkflowRun.Status.PARTIAL)
        self.assertEqual(result.details["steps"]["snapshot"]["status"], "not_run")
        snapshot.assert_not_called()

    def test_configuration_command_enables_daily_schedule(self):
        stdout = StringIO()

        call_command(
            "configure_deribit_options_schedule",
            "--enable",
            "--run-time",
            "09:25",
            stdout=stdout,
        )

        schedule = get_builtin_deribit_options_schedule()
        self.assertTrue(schedule.enabled)
        self.assertEqual(schedule.run_time, time(9, 25))
        self.assertIn("enabled", stdout.getvalue())

    @patch("apps.scheduling.deribit_workflow.execute_deribit_options_workflow")
    def test_manual_workflow_creates_manual_run(self, execute):
        execute.side_effect = lambda run: run

        run = execute_manual_deribit_options_workflow(dvol_lookback_days=4)

        self.assertEqual(run.trigger, DeribitOptionsWorkflowRun.Trigger.MANUAL)
        self.assertIsNone(run.schedule)
        self.assertEqual(run.dvol_lookback_days, 4)
