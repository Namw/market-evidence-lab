from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.inspection.models import KlineInspectionRun
from apps.scheduling.models import SchedulerHeartbeat, WorkflowRun
from apps.scheduling.services import (
    calculate_next_run_at,
    calculate_utc_range,
    claim_due_schedules,
    execute_workflow,
    get_builtin_schedule,
    record_heartbeat,
    scheduler_status,
)


FIXED_NOW = datetime(2026, 7, 31, 13, 45, 12, tzinfo=UTC)


def collection_result(run_id, status=CollectionRun.Status.SUCCESS):
    return SimpleNamespace(pk=run_id, status=status)


def inspection_result(
    run_id,
    status=KlineInspectionRun.Status.SUCCESS,
    quality_status=KlineInspectionRun.QualityStatus.PASSED,
):
    return SimpleNamespace(pk=run_id, status=status, quality_status=quality_status)


class TimeCalculationTests(TestCase):
    def test_utc_range_uses_current_utc_date_boundary(self):
        start, end = calculate_utc_range(1, now=FIXED_NOW)

        self.assertEqual(end, datetime(2026, 7, 31, tzinfo=UTC))
        self.assertEqual(start, datetime(2026, 7, 30, tzinfo=UTC))

    def test_default_three_day_range_contains_three_complete_days(self):
        schedule = get_builtin_schedule()

        start, end = calculate_utc_range(schedule.lookback_days, now=FIXED_NOW)

        self.assertEqual(schedule.lookback_days, 3)
        self.assertEqual(start, datetime(2026, 7, 28, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 7, 31, tzinfo=UTC))

    def test_next_run_uses_asia_shanghai_local_time(self):
        before_run = datetime(2026, 7, 31, 0, 0, tzinfo=UTC)  # 08:00 Shanghai
        after_run = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)  # 09:00 Shanghai

        self.assertEqual(
            calculate_next_run_at(time(8, 5), after=before_run),
            datetime(2026, 7, 31, 0, 5, tzinfo=UTC),
        )
        self.assertEqual(
            calculate_next_run_at(time(8, 5), after=after_run),
            datetime(2026, 8, 1, 0, 5, tzinfo=UTC),
        )


class ScheduleClaimTests(TestCase):
    def setUp(self):
        self.schedule = get_builtin_schedule()

    def test_disabled_due_schedule_is_not_claimed(self):
        self.schedule.enabled = False
        self.schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        self.schedule.save()

        self.assertEqual(claim_due_schedules(now=FIXED_NOW), [])
        self.assertFalse(WorkflowRun.objects.exists())

    def test_due_schedule_is_claimed_and_timestamps_are_advanced(self):
        self.schedule.enabled = True
        self.schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        self.schedule.save()

        claimed = claim_due_schedules(now=FIXED_NOW)

        self.assertEqual(len(claimed), 1)
        run = WorkflowRun.objects.get(pk=claimed[0])
        self.assertEqual(run.schedule, self.schedule)
        self.assertEqual(run.trigger, WorkflowRun.Trigger.SCHEDULED)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.last_run_at, FIXED_NOW)
        self.assertEqual(
            self.schedule.next_run_at,
            calculate_next_run_at(self.schedule.run_time, after=FIXED_NOW),
        )

    def test_not_due_schedule_is_not_claimed(self):
        self.schedule.enabled = True
        self.schedule.next_run_at = FIXED_NOW + timedelta(seconds=1)
        self.schedule.save()

        self.assertEqual(claim_due_schedules(now=FIXED_NOW), [])

    def test_second_claim_does_not_duplicate_the_same_due_run(self):
        self.schedule.enabled = True
        self.schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        self.schedule.save()

        first = claim_due_schedules(now=FIXED_NOW)
        second = claim_due_schedules(now=FIXED_NOW)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(WorkflowRun.objects.count(), 1)


class ConcurrentScheduleClaimTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_claimers_create_only_one_workflow_run(self):
        schedule = get_builtin_schedule()
        schedule.enabled = True
        schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        schedule.save()
        barrier = Barrier(2)

        def claim():
            close_old_connections()
            barrier.wait(timeout=5)
            try:
                return claim_due_schedules(now=FIXED_NOW)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in [pool.submit(claim), pool.submit(claim)]]

        self.assertEqual(sum(len(result) for result in results), 1)
        self.assertEqual(WorkflowRun.objects.count(), 1)


@patch("apps.scheduling.services.inspect_klines")
@patch("apps.scheduling.services.collect_klines")
class WorkflowExecutionTests(TestCase):
    def test_four_existing_services_run_in_required_order(self, collect, inspect):
        parent = Mock()
        parent.attach_mock(collect, "collect")
        parent.attach_mock(inspect, "inspect")
        collect.side_effect = [collection_result(11), collection_result(13)]
        inspect.side_effect = [inspection_result(12), inspection_result(14)]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(
            [item[0] for item in parent.mock_calls],
            ["collect", "inspect", "collect", "inspect"],
        )
        self.assertEqual(collect.call_args_list[0].args[:2], ("ETHUSDT", "1d"))
        self.assertEqual(inspect.call_args_list[0].args[:2], ("ETHUSDT", "1d"))
        self.assertEqual(collect.call_args_list[1].args[:2], ("ETHUSDT", "1h"))
        self.assertEqual(inspect.call_args_list[1].args[:2], ("ETHUSDT", "1h"))
        self.assertEqual(run.status, WorkflowRun.Status.SUCCESS)

    def test_1d_failure_does_not_block_1h(self, collect, inspect):
        collect.side_effect = [RuntimeError("1d failed"), collection_result(23)]
        inspect.side_effect = [inspection_result(22), inspection_result(24)]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(collect.call_count, 2)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(run.status, WorkflowRun.Status.PARTIAL)
        self.assertEqual(run.details["collection_1h_run_id"], 23)

    def test_collection_failure_still_runs_matching_inspection(self, collect, inspect):
        collect.side_effect = [RuntimeError("secret response body"), collection_result(33)]
        inspect.side_effect = [inspection_result(32), inspection_result(34)]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(inspect.call_args_list[0].args[1], "1d")
        self.assertNotIn("secret response body", run.error_message)
        self.assertIn("RuntimeError", run.error_message)

    def test_quality_issues_do_not_turn_success_into_execution_failure(
        self, collect, inspect
    ):
        collect.side_effect = [collection_result(41), collection_result(43)]
        inspect.side_effect = [
            inspection_result(42, quality_status=KlineInspectionRun.QualityStatus.ISSUES),
            inspection_result(44),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(run.status, WorkflowRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, WorkflowRun.QualityStatus.ISSUES)

    def test_success_partial_and_failed_status_semantics(self, collect, inspect):
        cases = (
            (
                [collection_result(51), collection_result(53)],
                [inspection_result(52), inspection_result(54)],
                WorkflowRun.Status.SUCCESS,
            ),
            (
                [collection_result(61, CollectionRun.Status.FAILED), collection_result(63)],
                [inspection_result(62), inspection_result(64)],
                WorkflowRun.Status.PARTIAL,
            ),
            (
                [collection_result(71, CollectionRun.Status.FAILED), collection_result(73, CollectionRun.Status.FAILED)],
                [
                    inspection_result(72, KlineInspectionRun.Status.FAILED),
                    inspection_result(74, KlineInspectionRun.Status.FAILED),
                ],
                WorkflowRun.Status.FAILED,
            ),
        )
        for collection_runs, inspection_runs, expected in cases:
            with self.subTest(expected=expected):
                collect.side_effect = collection_runs
                inspect.side_effect = inspection_runs
                run = execute_workflow(lookback_days=3, now=FIXED_NOW)
                self.assertEqual(run.status, expected)

    def test_passed_issues_and_unknown_quality_semantics(self, collect, inspect):
        cases = (
            (
                [inspection_result(82), inspection_result(84)],
                WorkflowRun.QualityStatus.PASSED,
            ),
            (
                [
                    inspection_result(92, quality_status=KlineInspectionRun.QualityStatus.ISSUES),
                    inspection_result(94),
                ],
                WorkflowRun.QualityStatus.ISSUES,
            ),
            (
                [
                    inspection_result(102, KlineInspectionRun.Status.FAILED),
                    inspection_result(104, KlineInspectionRun.Status.FAILED),
                ],
                WorkflowRun.QualityStatus.UNKNOWN,
            ),
        )
        for inspection_runs, expected in cases:
            with self.subTest(expected=expected):
                collect.side_effect = [collection_result(81), collection_result(83)]
                inspect.side_effect = inspection_runs
                run = execute_workflow(lookback_days=3, now=FIXED_NOW)
                self.assertEqual(run.quality_status, expected)

    def test_all_child_run_ids_are_written_to_details(self, collect, inspect):
        collect.side_effect = [collection_result(111), collection_result(113)]
        inspect.side_effect = [inspection_result(112), inspection_result(114)]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(run.details["collection_1d_run_id"], 111)
        self.assertEqual(run.details["inspection_1d_run_id"], 112)
        self.assertEqual(run.details["collection_1h_run_id"], 113)
        self.assertEqual(run.details["inspection_1h_run_id"], 114)
        self.assertEqual(set(run.details["steps"]), {
            "collection_1d", "inspection_1d", "collection_1h", "inspection_1h"
        })

    def test_manual_workflow_has_no_schedule_and_uses_manual_child_trigger(
        self, collect, inspect
    ):
        collect.side_effect = [collection_result(121), collection_result(123)]
        inspect.side_effect = [inspection_result(122), inspection_result(124)]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertIsNone(run.schedule)
        self.assertEqual(run.trigger, WorkflowRun.Trigger.MANUAL)
        self.assertEqual(collect.call_args_list[0].kwargs["trigger"], "manual")


class HeartbeatTests(TestCase):
    def test_recent_running_heartbeat_is_online(self):
        record_heartbeat("executor-a", poll_interval_seconds=30, now=FIXED_NOW)

        status = scheduler_status(now=FIXED_NOW + timedelta(seconds=89))

        self.assertTrue(status["online"])
        self.assertEqual(status["last_heartbeat_at"], FIXED_NOW)

    def test_stale_or_stopped_heartbeat_is_offline(self):
        record_heartbeat("stale", poll_interval_seconds=30, now=FIXED_NOW)
        record_heartbeat(
            "stopped",
            poll_interval_seconds=30,
            is_running=False,
            now=FIXED_NOW + timedelta(minutes=10),
        )

        status = scheduler_status(now=FIXED_NOW + timedelta(minutes=10))

        self.assertFalse(status["online"])
        self.assertEqual(
            status["last_heartbeat_at"],
            FIXED_NOW + timedelta(minutes=10),
        )

    def test_heartbeat_update_preserves_executor_start_time(self):
        record_heartbeat("executor-a", poll_interval_seconds=30, now=FIXED_NOW)
        record_heartbeat(
            "executor-a",
            poll_interval_seconds=30,
            now=FIXED_NOW + timedelta(seconds=30),
        )

        heartbeat = SchedulerHeartbeat.objects.get(executor_id="executor-a")
        self.assertEqual(heartbeat.started_at, FIXED_NOW)
        self.assertEqual(
            heartbeat.last_heartbeat_at,
            FIXED_NOW + timedelta(seconds=30),
        )
