from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase

from apps.news_analysis.models import NewsAnalysisRun, ObjectiveFactExtractionRun
from apps.scheduling.models import NewsAISchedule, NewsAIWorkflowRun
from apps.scheduling.news_ai_workflow import (
    claim_due_news_ai_schedules,
    execute_news_ai_workflow,
    get_builtin_news_ai_schedule,
)


NOW = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)


def make_analysis_run():
    return NewsAnalysisRun.objects.create(
        trigger=NewsAnalysisRun.Trigger.SCHEDULED,
        mode=NewsAnalysisRun.Mode.INCREMENTAL,
        analysis_version="test-v1",
        prompt_version="test-prompt-v1",
        model_name="deepseek-v4-flash",
        status=NewsAnalysisRun.Status.SUCCESS,
        candidate_count=2,
        success_count=2,
        api_request_count=1,
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        started_at=NOW,
        finished_at=NOW,
    )


def make_objective_run():
    return ObjectiveFactExtractionRun.objects.create(
        trigger=ObjectiveFactExtractionRun.Trigger.COMMAND,
        triggered_by="test",
        mode=ObjectiveFactExtractionRun.Mode.INCREMENTAL,
        status=ObjectiveFactExtractionRun.Status.SUCCESS,
        provider="DeepSeek",
        model="deepseek-v4-flash",
        prompt_version="test-objective-v1",
        generation_parameters={},
        started_at=NOW,
        finished_at=NOW,
        candidate_count=2,
        processed_count=2,
        request_count=2,
        success_count=2,
        prompt_tokens=200,
        completion_tokens=50,
        total_tokens=250,
    )


class NewsAIScheduleTests(TestCase):
    def test_builtin_schedule_is_disabled_at_0330_with_cost_limits(self):
        schedule = get_builtin_news_ai_schedule()

        self.assertFalse(schedule.enabled)
        self.assertEqual((schedule.run_time.hour, schedule.run_time.minute), (3, 30))
        self.assertEqual(schedule.max_direction_requests, 50)
        self.assertEqual(schedule.max_objective_records, 50)
        self.assertEqual(schedule.max_event_ai_calls, 100)

    def test_due_schedule_is_claimed_once_and_advanced(self):
        schedule = get_builtin_news_ai_schedule()
        schedule.enabled = True
        schedule.next_run_at = NOW - timedelta(minutes=1)
        schedule.save()

        claimed = claim_due_news_ai_schedules(now=NOW)

        self.assertEqual(len(claimed), 1)
        run = NewsAIWorkflowRun.objects.get(pk=claimed[0])
        self.assertEqual(run.trigger, NewsAIWorkflowRun.Trigger.SCHEDULED)
        self.assertEqual(run.max_objective_records, 50)
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_run_at, NOW)
        self.assertGreater(schedule.next_run_at, NOW)
        self.assertEqual(claim_due_news_ai_schedules(now=NOW), [])

    @patch("apps.scheduling.news_ai_workflow.event_merge_inputs_changed", return_value=False)
    @patch("apps.scheduling.news_ai_workflow.run_objective_fact_extraction")
    @patch("apps.scheduling.news_ai_workflow.run_news_analysis")
    def test_ai_workflow_passes_all_cost_limits_and_aggregates_usage(
        self,
        analyze,
        extract,
        inputs_changed,
    ):
        schedule = get_builtin_news_ai_schedule()
        schedule.max_direction_requests = 7
        schedule.max_objective_records = 11
        schedule.max_event_ai_calls = 13
        schedule.save()
        analyze.return_value = make_analysis_run()
        extract.return_value = make_objective_run()

        run = execute_news_ai_workflow(
            trigger=NewsAIWorkflowRun.Trigger.SCHEDULED,
            schedule=schedule,
        )

        self.assertEqual(run.status, NewsAIWorkflowRun.Status.SUCCESS)
        self.assertEqual(run.request_count, 3)
        self.assertEqual(run.total_tokens, 400)
        self.assertEqual(analyze.call_args.kwargs["max_requests"], 7)
        self.assertEqual(extract.call_args.kwargs["max_records"], 11)
        self.assertTrue(inputs_changed.called)

    @patch("apps.scheduling.news_ai_workflow.run_event_merge")
    @patch(
        "apps.scheduling.news_ai_workflow.estimate_event_merge_work",
        return_value={"estimated_ai_calls": 101},
    )
    @patch("apps.scheduling.news_ai_workflow.event_merge_inputs_changed", return_value=True)
    @patch("apps.scheduling.news_ai_workflow.run_objective_fact_extraction")
    @patch("apps.scheduling.news_ai_workflow.run_news_analysis")
    def test_event_merge_is_deferred_before_exceeding_limit(
        self,
        analyze,
        extract,
        _inputs_changed,
        _estimate,
        merge,
    ):
        analyze.return_value = make_analysis_run()
        extract.return_value = make_objective_run()

        run = execute_news_ai_workflow(schedule=get_builtin_news_ai_schedule())

        self.assertEqual(run.status, NewsAIWorkflowRun.Status.PARTIAL)
        self.assertEqual(run.event_merge_status, NewsAIWorkflowRun.StepStatus.NOT_RUN)
        self.assertIn("超过本轮上限", run.safe_error_summary)
        merge.assert_not_called()


class NewsAISchedulePageTests(TestCase):
    url = "/system/schedules/"

    def test_ai_configuration_is_saved_independently(self):
        response = self.client.post(
            self.url,
            {
                "action": "save_news_ai",
                "enabled": "on",
                "run_time": "03:30",
                "max_direction_requests": "20",
                "max_objective_records": "30",
                "max_event_ai_calls": "40",
            },
        )

        self.assertRedirects(response, self.url)
        schedule = NewsAISchedule.objects.get()
        self.assertTrue(schedule.enabled)
        self.assertEqual(schedule.max_direction_requests, 20)
        self.assertEqual(schedule.max_objective_records, 30)
        self.assertEqual(schedule.max_event_ai_calls, 40)
