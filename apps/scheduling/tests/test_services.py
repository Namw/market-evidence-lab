from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.inspection.models import KlineInspectionRun
from apps.scheduling.models import SchedulerHeartbeat, WorkflowRun
from apps.scheduling.services import (
    calculate_next_interval_run_at,
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


def pipeline_result(
    collection_id,
    inspection_id,
    *,
    collection_status=CollectionRun.Status.SUCCESS,
    inspection_status=KlineInspectionRun.Status.SUCCESS,
    quality_status=KlineInspectionRun.QualityStatus.PASSED,
):
    return SimpleNamespace(
        collection_run=collection_result(collection_id, collection_status),
        inspection_run=inspection_result(
            inspection_id,
            inspection_status,
            quality_status,
        ),
    )


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

    def test_interval_schedule_uses_six_hour_slots_from_daily_anchor(self):
        anchor = time(8, 35)

        self.assertEqual(
            calculate_next_interval_run_at(
                anchor,
                interval_hours=6,
                after=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
            ),
            datetime(2026, 8, 1, 6, 35, tzinfo=UTC),
        )
        self.assertEqual(
            calculate_next_interval_run_at(
                anchor,
                interval_hours=6,
                after=datetime(2026, 8, 1, 19, 0, tzinfo=UTC),
            ),
            datetime(2026, 8, 2, 0, 35, tzinfo=UTC),
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


@patch("apps.scheduling.services.collect_and_inspect")
class WorkflowExecutionTests(TestCase):
    def test_four_collection_and_inspection_pipelines_run_in_required_order(self, pipeline):
        pipeline.side_effect = [
            pipeline_result(11, 12),
            pipeline_result(13, 14),
            pipeline_result(15, 16),
            pipeline_result(17, 18),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(
            [item.kwargs["data_type"] for item in pipeline.call_args_list],
            ["kline", "kline", "open_interest", "funding"],
        )
        self.assertEqual(pipeline.call_args_list[0].kwargs["interval"], "1d")
        self.assertEqual(pipeline.call_args_list[1].kwargs["interval"], "1h")
        self.assertEqual(run.status, WorkflowRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, WorkflowRun.QualityStatus.PASSED)

    def test_1d_failure_does_not_block_remaining_pipelines(self, pipeline):
        pipeline.side_effect = [
            RuntimeError("1d failed"),
            pipeline_result(23, 24),
            pipeline_result(25, 26),
            pipeline_result(27, 28),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(pipeline.call_count, 4)
        self.assertEqual(run.status, WorkflowRun.Status.PARTIAL)
        self.assertEqual(run.details["collection_1h_run_id"], 23)
        self.assertEqual(run.quality_status, WorkflowRun.QualityStatus.UNKNOWN)

    def test_pipeline_exception_is_safely_summarized(self, pipeline):
        pipeline.side_effect = [
            RuntimeError("secret response body"),
            pipeline_result(33, 34),
            pipeline_result(35, 36),
            pipeline_result(37, 38),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertNotIn("secret response body", run.error_message)
        self.assertIn("RuntimeError", run.error_message)

    def test_quality_issues_do_not_turn_collection_success_into_failure(self, pipeline):
        pipeline.side_effect = [
            pipeline_result(41, 42, quality_status=KlineInspectionRun.QualityStatus.ISSUES),
            pipeline_result(43, 44),
            pipeline_result(45, 46),
            pipeline_result(47, 48),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(run.status, WorkflowRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, WorkflowRun.QualityStatus.ISSUES)

    def test_all_execution_steps_failed_sets_workflow_failed(self, pipeline):
        pipeline.side_effect = [
            pipeline_result(
                index,
                index + 1,
                collection_status=CollectionRun.Status.FAILED,
                inspection_status=KlineInspectionRun.Status.FAILED,
            )
            for index in (51, 53, 55, 57)
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
        self.assertEqual(run.quality_status, WorkflowRun.QualityStatus.UNKNOWN)

    def test_four_checks_must_all_complete_and_pass_for_overall_passed(self, pipeline):
        cases = (
            (
                [pipeline_result(index, index + 1) for index in (81, 83, 85, 87)],
                WorkflowRun.QualityStatus.PASSED,
            ),
            (
                [
                    pipeline_result(91, 92, quality_status=KlineInspectionRun.QualityStatus.ISSUES),
                    pipeline_result(93, 94),
                    pipeline_result(95, 96),
                    pipeline_result(97, 98),
                ],
                WorkflowRun.QualityStatus.ISSUES,
            ),
            (
                [
                    pipeline_result(101, 102, inspection_status=KlineInspectionRun.Status.FAILED),
                    pipeline_result(103, 104),
                    pipeline_result(105, 106),
                    pipeline_result(107, 108),
                ],
                WorkflowRun.QualityStatus.UNKNOWN,
            ),
        )
        for pipeline_runs, expected in cases:
            with self.subTest(expected=expected):
                pipeline.side_effect = pipeline_runs
                run = execute_workflow(lookback_days=3, now=FIXED_NOW)
                self.assertEqual(run.quality_status, expected)

    def test_all_child_run_ids_are_written_to_details(self, pipeline):
        pipeline.side_effect = [
            pipeline_result(111, 112),
            pipeline_result(113, 114),
            pipeline_result(115, 116),
            pipeline_result(117, 118),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertEqual(run.details["collection_1d_run_id"], 111)
        self.assertEqual(run.details["inspection_1d_run_id"], 112)
        self.assertEqual(run.details["collection_1h_run_id"], 113)
        self.assertEqual(run.details["inspection_1h_run_id"], 114)
        self.assertEqual(run.details["collection_oi_run_id"], 115)
        self.assertEqual(run.details["inspection_oi_run_id"], 116)
        self.assertEqual(run.details["collection_funding_run_id"], 117)
        self.assertEqual(run.details["inspection_funding_run_id"], 118)
        self.assertEqual(set(run.details["steps"]), {
            "collection_1d", "inspection_1d", "collection_1h", "inspection_1h",
            "collection_oi", "inspection_oi", "collection_funding", "inspection_funding",
        })

    def test_manual_workflow_has_no_schedule_and_uses_manual_child_trigger(self, pipeline):
        pipeline.side_effect = [
            pipeline_result(121, 122),
            pipeline_result(123, 124),
            pipeline_result(125, 126),
            pipeline_result(127, 128),
        ]

        run = execute_workflow(lookback_days=3, now=FIXED_NOW)

        self.assertIsNone(run.schedule)
        self.assertEqual(run.trigger, WorkflowRun.Trigger.MANUAL)
        self.assertEqual(pipeline.call_args_list[0].kwargs["trigger"], "manual")


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
