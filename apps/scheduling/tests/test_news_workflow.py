from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.collection.models import CollectionRun, SourceNetworkPolicy
from apps.inspection.models import NewsInspectionRun
from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun
from apps.news_analysis.tests.helpers import make_record
from apps.news_data.models import NewsFeed, NewsRawRecord, NewsSource
from apps.news_data.sources import (
    BINANCE_ANNOUNCEMENTS_CODE,
    BLS_CPI_CODE,
    BLS_EMPLOYMENT_SITUATION_CODE,
    BLS_PPI_CODE,
    COINDESK_CODE,
    ETHEREUM_FOUNDATION_CODE,
    FED_MONETARY_POLICY_CODE,
    FEED_DEFINITIONS,
    SOURCE_DEFINITIONS,
)
from apps.scheduling.models import (
    NewsWorkflowFeedRun,
    NewsWorkflowRun,
    NewsWorkflowSchedule,
)
from apps.scheduling.news_workflow import (
    NEWS_FEED_GROUP_CODES,
    NewsWorkflowAlreadyRunning,
    _create_news_workflow_run,
    claim_due_news_schedules,
    execute_claimed_news_workflow,
    execute_news_workflow,
    get_builtin_news_schedule,
    get_builtin_news_schedules,
)


FIXED_NOW = datetime(2026, 8, 1, 2, 30, tzinfo=UTC)


def child_pipeline(
    source_code,
    *,
    collection_status=CollectionRun.Status.SUCCESS,
    quality_status=NewsInspectionRun.QualityStatus.PASSED,
    inspection_status=NewsInspectionRun.Status.SUCCESS,
    inserted=1,
    updated=2,
    skipped=3,
    reasons=None,
):
    definition = SOURCE_DEFINITIONS[source_code]
    source, _ = NewsSource.objects.get_or_create(
        code=source_code,
        defaults={
            "name": definition.name,
            "enabled": True,
            "activated_at": FIXED_NOW - timedelta(days=30),
            "source_type": NewsSource.SourceType.OFFICIAL,
            "collection_method": definition.collection_method,
            "observation_scope": definition.observation_scope,
            "base_url": definition.base_url,
            "feed_url": definition.feed_url,
            "parser_version": definition.parser_version,
        },
    )
    collection = CollectionRun.objects.create(
        data_type=CollectionRun.DataType.NEWS,
        news_source=source,
        range_start=FIXED_NOW - timedelta(days=3),
        range_end=FIXED_NOW,
        trigger=CollectionRun.Trigger.MANUAL,
        status=collection_status,
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
        inserted_count=inserted,
        updated_count=updated,
        skipped_count=skipped,
    )
    inspection = NewsInspectionRun.objects.create(
        source=source,
        range_start=collection.range_start,
        range_end=collection.range_end,
        trigger=NewsInspectionRun.Trigger.MANUAL,
        status=inspection_status,
        quality_status=quality_status,
        reasons=reasons or [],
        source_collection_run=collection,
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
    )
    return SimpleNamespace(collection_run=collection, inspection_run=inspection)


def analysis_run(
    *,
    status=NewsAnalysisRun.Status.SUCCESS,
    candidate=0,
    success=0,
    failure=0,
    skipped=0,
    safe_summary="",
    trigger=NewsAnalysisRun.Trigger.MANUAL,
):
    return NewsAnalysisRun.objects.create(
        trigger=trigger,
        mode=NewsAnalysisRun.Mode.INCREMENTAL,
        analysis_version="news-v1",
        prompt_version="prompt-v1",
        model_name="mock-model",
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
        status=status,
        candidate_count=candidate,
        success_count=success,
        failure_count=failure,
        skipped_count=skipped,
        safe_error_summary=safe_summary,
    )


@patch("apps.scheduling.news_workflow.run_news_analysis")
@patch("apps.scheduling.news_workflow.inspect_news_collection")
@patch("apps.scheduling.news_workflow.collect_news_feed")
class NewsWorkflowExecutionTests(TestCase):
    def setUp(self):
        NewsFeed.objects.exclude(
            code__in=[ETHEREUM_FOUNDATION_CODE, BINANCE_ANNOUNCEMENTS_CODE]
        ).update(enabled=False)

    def test_two_sources_and_analysis_succeed_with_correct_links(self, collect, inspect, analyze):
        ethereum = child_pipeline(ETHEREUM_FOUNDATION_CODE)
        binance = child_pipeline(BINANCE_ANNOUNCEMENTS_CODE, inserted=4, updated=5, skipped=6)
        analysis = analysis_run(candidate=7, success=7)
        order = []
        collections = iter((ethereum.collection_run, binance.collection_run))
        inspections = iter((ethereum.inspection_run, binance.inspection_run))

        def collect_step(source_code, **kwargs):
            order.append(f"collect:{source_code}")
            return next(collections)

        def inspect_step(collection_run):
            order.append(f"inspect:{collection_run.news_source.code}")
            return next(inspections)

        def analyze_step(**kwargs):
            order.append("analyze")
            return analysis

        collect.side_effect = collect_step
        inspect.side_effect = inspect_step
        analyze.side_effect = analyze_step

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(
            [call.args[0] for call in collect.call_args_list],
            [ETHEREUM_FOUNDATION_CODE, BINANCE_ANNOUNCEMENTS_CODE],
        )
        self.assertTrue(
            all("use_source_proxy" not in call.kwargs for call in collect.call_args_list)
        )
        self.assertEqual(
            order,
            [
                f"collect:{ETHEREUM_FOUNDATION_CODE}",
                f"collect:{BINANCE_ANNOUNCEMENTS_CODE}",
                f"inspect:{ETHEREUM_FOUNDATION_CODE}",
                f"inspect:{BINANCE_ANNOUNCEMENTS_CODE}",
                "analyze",
            ],
        )
        self.assertEqual(run.status, NewsWorkflowRun.Status.SUCCESS)
        self.assertEqual(run.ethereum_collection_run, ethereum.collection_run)
        self.assertEqual(run.ethereum_inspection_run, ethereum.inspection_run)
        self.assertEqual(run.binance_collection_run, binance.collection_run)
        self.assertEqual(run.binance_inspection_run, binance.inspection_run)
        self.assertEqual(run.analysis_run, analysis)
        self.assertEqual((run.inserted_count, run.updated_count, run.skipped_count), (5, 7, 9))
        self.assertEqual(
            (
                run.analysis_candidate_count,
                run.analysis_success_count,
                run.analysis_failure_count,
                run.analysis_skipped_count,
            ),
            (7, 7, 0, 0),
        )

    def test_coindesk_manual_workflow_only_collects_coindesk(
        self,
        collect,
        inspect,
        analyze,
    ):
        NewsFeed.objects.filter(code=COINDESK_CODE).update(enabled=True)
        coindesk = child_pipeline(COINDESK_CODE)
        collect.return_value = coindesk.collection_run
        inspect.return_value = coindesk.inspection_run
        analyze.return_value = analysis_run()

        run = execute_news_workflow(
            feed_group=NewsWorkflowSchedule.FeedGroup.COINDESK,
            range_end=FIXED_NOW,
        )

        self.assertEqual([call.args[0] for call in collect.call_args_list], [COINDESK_CODE])
        self.assertEqual(run.feed_group, NewsWorkflowSchedule.FeedGroup.COINDESK)
        self.assertEqual(run.feed_steps.count(), 1)

    def test_one_source_exception_does_not_block_other_source_or_analysis(self, collect, inspect, analyze):
        binance = child_pipeline(BINANCE_ANNOUNCEMENTS_CODE)
        collect.side_effect = [
            RuntimeError("secret-key-and-response"),
            binance.collection_run,
        ]
        inspect.return_value = binance.inspection_run
        analyze.return_value = analysis_run(candidate=1, success=1)

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(collect.call_count, 2)
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(run.status, NewsWorkflowRun.Status.PARTIAL)
        self.assertEqual(run.ethereum_collection_status, NewsWorkflowRun.StepStatus.FAILED)
        self.assertEqual(run.ethereum_quality_status, NewsWorkflowRun.QualityStatus.NOT_RUN)
        self.assertEqual(run.binance_collection_run, binance.collection_run)
        self.assertNotIn("secret-key-and-response", run.safe_error_summary)
        self.assertIn("RuntimeError", run.safe_error_summary)

    def test_both_sources_fail_and_zero_candidate_analysis_is_overall_failed(self, collect, inspect, analyze):
        collect.side_effect = [RuntimeError("one"), ValueError("two")]
        analyze.return_value = analysis_run(candidate=0, success=0)

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(collect.call_count, 2)
        inspect.assert_not_called()
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(run.status, NewsWorkflowRun.Status.FAILED)
        self.assertEqual(run.analysis_status, NewsWorkflowRun.StepStatus.SUCCESS)

    def test_both_sources_fail_but_legacy_candidate_is_analyzed(self, collect, inspect, analyze):
        collect.side_effect = [RuntimeError("one"), RuntimeError("two")]
        analyze.return_value = analysis_run(candidate=1, success=1)

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(run.status, NewsWorkflowRun.Status.PARTIAL)
        self.assertEqual(run.analysis_success_count, 1)

    def test_quality_warning_records_issues_and_analysis_still_runs(self, collect, inspect, analyze):
        ethereum = child_pipeline(
            ETHEREUM_FOUNDATION_CODE,
            quality_status=NewsInspectionRun.QualityStatus.WARNING,
            reasons=["retry", "history limited"],
        )
        binance = child_pipeline(BINANCE_ANNOUNCEMENTS_CODE)
        collect.side_effect = [ethereum.collection_run, binance.collection_run]
        inspect.side_effect = [ethereum.inspection_run, binance.inspection_run]
        analyze.return_value = analysis_run(candidate=2, success=2)

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(run.status, NewsWorkflowRun.Status.PARTIAL)
        self.assertEqual(run.ethereum_collection_status, NewsWorkflowRun.StepStatus.SUCCESS)
        self.assertEqual(run.ethereum_quality_status, NewsWorkflowRun.QualityStatus.WARNING)
        self.assertEqual(run.quality_issue_count, 2)
        analyze.assert_called_once()

    def test_unconfigured_analysis_is_partial_without_changing_collection(self, collect, inspect, analyze):
        ethereum = child_pipeline(ETHEREUM_FOUNDATION_CODE)
        binance = child_pipeline(BINANCE_ANNOUNCEMENTS_CODE)
        collect.side_effect = [ethereum.collection_run, binance.collection_run]
        inspect.side_effect = [ethereum.inspection_run, binance.inspection_run]
        analyze.return_value = analysis_run(
            status=NewsAnalysisRun.Status.NOT_RUN,
            candidate=3,
            skipped=3,
            safe_summary="DeepSeek API 未配置，新闻增量分析未执行。",
        )

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(run.status, NewsWorkflowRun.Status.PARTIAL)
        self.assertEqual(run.analysis_status, NewsWorkflowRun.StepStatus.NOT_RUN)
        self.assertEqual(run.ethereum_collection_status, NewsWorkflowRun.StepStatus.SUCCESS)
        self.assertEqual(run.binance_quality_status, NewsWorkflowRun.QualityStatus.PASSED)

    def test_partial_and_complete_analysis_failure_are_isolated(self, collect, inspect, analyze):
        for analysis_status, success, failure, expected in (
            (NewsAnalysisRun.Status.PARTIAL, 1, 1, NewsWorkflowRun.Status.PARTIAL),
            (NewsAnalysisRun.Status.FAILED, 0, 2, NewsWorkflowRun.Status.PARTIAL),
        ):
            with self.subTest(analysis_status=analysis_status):
                collect.side_effect = [
                    (ethereum := child_pipeline(ETHEREUM_FOUNDATION_CODE)).collection_run,
                    (binance := child_pipeline(BINANCE_ANNOUNCEMENTS_CODE)).collection_run,
                ]
                inspect.side_effect = [ethereum.inspection_run, binance.inspection_run]
                analyze.return_value = analysis_run(
                    status=analysis_status,
                    candidate=2,
                    success=success,
                    failure=failure,
                    safe_summary="AI service failure.",
                )
                run = execute_news_workflow(range_end=FIXED_NOW)
                self.assertEqual(run.status, expected)
                self.assertEqual(run.analysis_failure_count, failure)


class RealAnalysisIntegrationTests(TestCase):
    def setUp(self):
        NewsFeed.objects.exclude(
            code__in=[ETHEREUM_FOUNDATION_CODE, BINANCE_ANNOUNCEMENTS_CODE]
        ).update(enabled=False)
        self.ethereum = child_pipeline(ETHEREUM_FOUNDATION_CODE)
        self.binance = child_pipeline(BINANCE_ANNOUNCEMENTS_CODE)

    def collections(self, source_code, **kwargs):
        pipeline = self.ethereum if source_code == ETHEREUM_FOUNDATION_CODE else self.binance
        return pipeline.collection_run

    def inspections(self, collection_run):
        if collection_run == self.ethereum.collection_run:
            return self.ethereum.inspection_run
        return self.binance.inspection_run

    @override_settings(NEWS_AI_API_KEY="")
    @patch("apps.scheduling.news_workflow.inspect_news_collection")
    @patch("apps.scheduling.news_workflow.collect_news_feed")
    def test_deepseek_unconfigured_creates_linked_not_run_analysis(self, collect, inspect):
        collect.side_effect = self.collections
        inspect.side_effect = self.inspections
        make_record(title="Material legacy event")

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(run.analysis_status, NewsAnalysisRun.Status.NOT_RUN)
        self.assertIsNotNone(run.analysis_run)
        self.assertEqual(run.analysis_candidate_count, 1)
        self.assertEqual(run.analysis_skipped_count, 1)
        self.assertEqual(run.analysis_run.api_request_count, 0)
        self.assertEqual(run.status, NewsWorkflowRun.Status.PARTIAL)

    @override_settings(NEWS_AI_API_KEY="")
    @patch("apps.scheduling.news_workflow.inspect_news_collection")
    @patch("apps.scheduling.news_workflow.collect_news_feed")
    def test_zero_candidates_is_success_and_does_not_require_deepseek(self, collect, inspect):
        collect.side_effect = self.collections
        inspect.side_effect = self.inspections

        run = execute_news_workflow(range_end=FIXED_NOW)

        self.assertEqual(run.analysis_candidate_count, 0)
        self.assertEqual(run.analysis_status, NewsAnalysisRun.Status.SUCCESS)
        self.assertEqual(run.analysis_run.api_request_count, 0)
        self.assertEqual(run.status, NewsWorkflowRun.Status.SUCCESS)

    @override_settings(NEWS_AI_API_KEY="")
    @patch("apps.scheduling.news_workflow.inspect_news_collection")
    @patch("apps.scheduling.news_workflow.collect_news_feed")
    def test_irrelevant_rule_item_is_processed_deleted_and_absent_later(self, collect, inspect):
        collect.side_effect = self.collections
        inspect.side_effect = self.inspections
        record = make_record(title="Join the Trading Competition")

        first = execute_news_workflow(range_end=FIXED_NOW, analysis_client=object())
        collect.side_effect = self.collections
        inspect.side_effect = self.inspections
        second = execute_news_workflow(range_end=FIXED_NOW, analysis_client=object())

        self.assertEqual(first.analysis_candidate_count, 1)
        self.assertEqual(first.analysis_success_count, 1)
        self.assertFalse(NewsAnalysisResult.objects.filter(news_record_id=record.id).exists())
        self.assertFalse(NewsRawRecord.objects.filter(pk=record.id).exists())
        self.assertEqual(second.analysis_candidate_count, 0)
        self.assertEqual(second.analysis_success_count, 0)


class RegulatoryFeedWorkflowTests(TestCase):
    @patch("apps.scheduling.news_workflow.run_news_analysis")
    @patch("apps.scheduling.news_workflow.inspect_news_collection")
    @patch("apps.scheduling.news_workflow.collect_news_feed")
    def test_all_feeds_share_one_workflow_and_keep_independent_steps(
        self, collect, inspect, analyze
    ):
        def collect_step(feed_code, **kwargs):
            feed = NewsFeed.objects.select_related("source").get(code=feed_code)
            return CollectionRun.objects.create(
                data_type=CollectionRun.DataType.NEWS,
                news_source=feed.source,
                news_feed=feed,
                range_start=FIXED_NOW - timedelta(days=3),
                range_end=FIXED_NOW,
                trigger=CollectionRun.Trigger.MANUAL,
                status=CollectionRun.Status.SUCCESS,
                started_at=FIXED_NOW,
                finished_at=FIXED_NOW,
            )

        def inspect_step(collection_run):
            return NewsInspectionRun.objects.create(
                source=collection_run.news_source,
                feed=collection_run.news_feed,
                range_start=collection_run.range_start,
                range_end=collection_run.range_end,
                trigger=NewsInspectionRun.Trigger.MANUAL,
                status=NewsInspectionRun.Status.SUCCESS,
                quality_status=NewsInspectionRun.QualityStatus.PASSED,
                coverage_complete=True,
                source_collection_run=collection_run,
                started_at=FIXED_NOW,
                finished_at=FIXED_NOW,
            )

        collect.side_effect = collect_step
        inspect.side_effect = inspect_step
        analyze.return_value = analysis_run()

        run = execute_news_workflow(range_end=FIXED_NOW)

        core_feed_codes = [code for code in FEED_DEFINITIONS if code != COINDESK_CODE]
        self.assertEqual(
            [call.args[0] for call in collect.call_args_list],
            core_feed_codes,
        )
        self.assertEqual(
            NewsWorkflowFeedRun.objects.filter(workflow_run=run).count(),
            len(core_feed_codes),
        )
        self.assertEqual(run.status, NewsWorkflowRun.Status.SUCCESS)


class NewsScheduleTests(TestCase):
    def setUp(self):
        NewsFeed.objects.exclude(
            code__in=[ETHEREUM_FOUNDATION_CODE, BINANCE_ANNOUNCEMENTS_CODE]
        ).update(enabled=False)

    def test_due_schedule_claims_scheduled_run_and_advances_next_time(self):
        schedule = get_builtin_news_schedule()
        schedule.enabled = True
        schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        schedule.save()

        claimed = claim_due_news_schedules(now=FIXED_NOW)

        self.assertEqual(len(claimed), 1)
        run = NewsWorkflowRun.objects.get(pk=claimed[0])
        self.assertEqual(run.trigger, NewsWorkflowRun.Trigger.SCHEDULED)
        self.assertEqual(run.schedule, schedule)
        self.assertEqual(run.feed_group, NewsWorkflowSchedule.FeedGroup.CORE)
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_run_at, FIXED_NOW)
        self.assertGreater(schedule.next_run_at, FIXED_NOW)
        self.assertLessEqual(schedule.next_run_at - FIXED_NOW, timedelta(hours=24))

    def test_builtin_news_schedules_have_independent_groups_and_intervals(self):
        schedules = get_builtin_news_schedules()

        self.assertEqual(
            [schedule.feed_group for schedule in schedules],
            [
                NewsWorkflowSchedule.FeedGroup.CORE,
                NewsWorkflowSchedule.FeedGroup.COINDESK,
            ],
        )
        self.assertEqual([schedule.interval_hours for schedule in schedules], [24, 6])

    def test_fed_and_bls_feeds_belong_to_daily_core_workflow(self):
        core_codes = NEWS_FEED_GROUP_CODES[NewsWorkflowSchedule.FeedGroup.CORE]

        self.assertTrue(
            {
                FED_MONETARY_POLICY_CODE,
                BLS_EMPLOYMENT_SITUATION_CODE,
                BLS_CPI_CODE,
                BLS_PPI_CODE,
            }.issubset(core_codes)
        )
        self.assertEqual(get_builtin_news_schedule().interval_hours, 24)

    def test_two_due_news_schedules_are_claimed_serially_without_skipping(self):
        core, coindesk = get_builtin_news_schedules()
        for schedule in (core, coindesk):
            schedule.enabled = True
            schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
            schedule.save()

        first_claim = claim_due_news_schedules(now=FIXED_NOW)
        first_run = NewsWorkflowRun.objects.get(pk=first_claim[0])
        coindesk.refresh_from_db()

        self.assertEqual(first_run.feed_group, NewsWorkflowSchedule.FeedGroup.CORE)
        self.assertEqual(coindesk.next_run_at, FIXED_NOW - timedelta(minutes=1))

        first_run.status = NewsWorkflowRun.Status.SUCCESS
        first_run.finished_at = FIXED_NOW
        first_run.save(update_fields=["status", "finished_at"])
        second_claim = claim_due_news_schedules(now=FIXED_NOW)
        second_run = NewsWorkflowRun.objects.get(pk=second_claim[0])

        self.assertEqual(
            second_run.feed_group,
            NewsWorkflowSchedule.FeedGroup.COINDESK,
        )

    def test_disabled_schedule_is_not_claimed(self):
        schedule = get_builtin_news_schedule()
        schedule.enabled = False
        schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        schedule.save()

        self.assertEqual(claim_due_news_schedules(now=FIXED_NOW), [])

    def test_manual_running_workflow_blocks_scheduled_reentry(self):
        schedule = get_builtin_news_schedule()
        schedule.enabled = True
        schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        schedule.save()
        NewsWorkflowRun.objects.create(
            trigger=NewsWorkflowRun.Trigger.MANUAL,
            status=NewsWorkflowRun.Status.RUNNING,
            started_at=FIXED_NOW - timedelta(minutes=2),
        )

        claimed = claim_due_news_schedules(now=FIXED_NOW)

        self.assertEqual(claimed, [])
        self.assertEqual(NewsWorkflowRun.objects.count(), 1)
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, FIXED_NOW - timedelta(minutes=1))

    @patch("apps.scheduling.news_workflow.execute_news_workflow")
    def test_claimed_scheduled_entry_calls_same_workflow_service(self, execute):
        run = NewsWorkflowRun.objects.create(
            trigger=NewsWorkflowRun.Trigger.SCHEDULED,
            status=NewsWorkflowRun.Status.RUNNING,
            started_at=FIXED_NOW,
        )
        execute.return_value = run

        execute_claimed_news_workflow(run.pk)

        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs["workflow_run"], run)
        self.assertFalse(execute.call_args.kwargs["run_ai"])

    def test_scheduled_running_workflow_blocks_manual_entry(self):
        NewsWorkflowRun.objects.create(
            trigger=NewsWorkflowRun.Trigger.SCHEDULED,
            status=NewsWorkflowRun.Status.RUNNING,
            started_at=FIXED_NOW,
        )

        with self.assertRaises(NewsWorkflowAlreadyRunning):
            execute_news_workflow(range_end=FIXED_NOW)

    @patch("apps.scheduling.news_workflow.run_news_analysis")
    @patch("apps.scheduling.news_workflow.inspect_news_collection")
    @patch("apps.scheduling.news_workflow.collect_news_feed")
    def test_due_scheduled_run_only_collects_and_does_not_call_ai(
        self,
        collect,
        inspect,
        analyze,
    ):
        schedule = get_builtin_news_schedule()
        schedule.enabled = True
        schedule.next_run_at = FIXED_NOW - timedelta(minutes=1)
        schedule.save()
        ethereum = child_pipeline(ETHEREUM_FOUNDATION_CODE)
        binance = child_pipeline(BINANCE_ANNOUNCEMENTS_CODE)
        collect.side_effect = [ethereum.collection_run, binance.collection_run]
        inspect.side_effect = [ethereum.inspection_run, binance.inspection_run]
        claimed = claim_due_news_schedules(now=FIXED_NOW)
        run = execute_claimed_news_workflow(claimed[0])

        self.assertEqual(run.status, NewsWorkflowRun.Status.SUCCESS)
        self.assertTrue(
            all(
                call.kwargs["trigger"] == NewsWorkflowRun.Trigger.SCHEDULED
                for call in collect.call_args_list
            )
        )
        self.assertTrue(
            all("use_source_proxy" not in call.kwargs for call in collect.call_args_list)
        )
        self.assertEqual(run.analysis_status, NewsWorkflowRun.StepStatus.NOT_RUN)
        analyze.assert_not_called()


class NewsWorkflowPageTests(TestCase):
    url = "/system/schedules/"

    def test_page_displays_news_configuration_and_separate_status_columns(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "任务列表")
        self.assertContains(response, "任务列表 <span>9</span>", html=True)
        self.assertContains(response, "官方与监管新闻工作流")
        self.assertContains(response, "CoinDesk 新闻工作流")
        self.assertContains(response, "Ethereum Foundation、Binance")
        self.assertContains(response, "Fed、BLS")
        self.assertContains(response, "SlowMist Hacked 与 Circle Pressroom")
        self.assertContains(response, "新闻 DeepSeek 增量分析")
        self.assertContains(response, "不会调用 DeepSeek")
        self.assertContains(response, "只采集 CoinDesk")
        self.assertContains(response, "每天")
        self.assertContains(response, "每 6 小时")
        self.assertContains(response, "手动调度")

    def test_news_configuration_saves_without_changing_kline_schedule(self):
        news_schedule = get_builtin_news_schedule()
        kline_enabled = self.client.get(self.url).context["schedule"].enabled

        response = self.client.post(
            self.url,
            {
                "action": "save_news",
                "enabled": "on",
                "run_time": "09:40",
            },
        )

        self.assertRedirects(response, self.url)
        news_schedule.refresh_from_db()
        self.assertTrue(news_schedule.enabled)
        self.assertEqual((news_schedule.run_time.hour, news_schedule.run_time.minute), (9, 40))
        self.assertEqual(news_schedule.interval_hours, 24)
        self.assertEqual(self.client.get(self.url).context["schedule"].enabled, kline_enabled)

    def test_coindesk_configuration_is_saved_independently(self):
        core = get_builtin_news_schedule(NewsWorkflowSchedule.FeedGroup.CORE)
        coindesk = get_builtin_news_schedule(NewsWorkflowSchedule.FeedGroup.COINDESK)

        response = self.client.post(
            self.url,
            {"action": "save_coindesk", "enabled": "on", "run_time": "10:15"},
        )

        self.assertRedirects(response, self.url)
        core.refresh_from_db()
        coindesk.refresh_from_db()
        self.assertFalse(core.enabled)
        self.assertTrue(coindesk.enabled)
        self.assertEqual((coindesk.run_time.hour, coindesk.run_time.minute), (10, 15))
        self.assertEqual(coindesk.interval_hours, 6)

    @patch("apps.scheduling.views.execute_news_workflow")
    def test_manual_entry_calls_unified_service_and_duplicate_token_is_rejected(self, execute):
        execute.return_value = SimpleNamespace(
            pk=91,
            status=NewsWorkflowRun.Status.SUCCESS,
        )
        token = self.client.get(self.url).context["news_run_token"]
        payload = {
            "action": "run_news",
            "news_run_token": token,
        }

        response = self.client.post(self.url, payload)
        duplicate = self.client.post(self.url, payload, follow=True)

        self.assertRedirects(
            response,
            "/system/schedules/runs/news/91/",
            fetch_redirect_response=False,
        )
        execute.assert_called_once_with(
            trigger=NewsWorkflowRun.Trigger.MANUAL,
            schedule=None,
            feed_group=NewsWorkflowSchedule.FeedGroup.CORE,
            run_ai=False,
        )
        self.assertContains(duplicate, "未重复执行")

    @override_settings(SOURCE_PROXY_URL="http://127.0.0.1:7897")
    def test_source_network_page_uses_recommended_defaults_and_saves_routes(self):
        response = self.client.get("/system/schedules/sources/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "来源与网络")
        self.assertTrue(
            SourceNetworkPolicy.objects.get(source_key="coindesk").use_proxy
        )
        self.assertTrue(SourceNetworkPolicy.objects.get(source_key="sec").use_proxy)
        self.assertTrue(
            SourceNetworkPolicy.objects.get(source_key="deribit").use_proxy
        )
        self.assertFalse(
            SourceNetworkPolicy.objects.get(source_key="defillama").use_proxy
        )

        payload = {"route_coindesk": "direct"}
        payload.update(
            {
                f"route_{row['key']}": (
                    "proxy" if row["policy"].use_proxy else "direct"
                )
                for row in response.context["source_rows"]
            }
        )
        payload["route_coindesk"] = "direct"
        saved = self.client.post("/system/schedules/sources/", payload)

        self.assertRedirects(saved, "/system/schedules/sources/")
        self.assertFalse(
            SourceNetworkPolicy.objects.get(source_key="coindesk").use_proxy
        )

    def test_future_news_source_defaults_to_direct(self):
        NewsSource.objects.create(
            code="future_source",
            name="Future Source",
            enabled=True,
            activated_at=FIXED_NOW,
            source_type=NewsSource.SourceType.OFFICIAL,
            collection_method="rss",
            observation_scope="test",
            base_url="https://future.example",
            feed_url="https://future.example/rss",
            parser_version="test-v1",
        )

        self.client.get("/system/schedules/sources/")

        self.assertFalse(
            SourceNetworkPolicy.objects.get(source_key="future_source").use_proxy
        )

    @patch("apps.scheduling.views.execute_news_workflow")
    def test_coindesk_manual_entry_only_requests_coindesk_group(self, execute):
        execute.return_value = SimpleNamespace(
            pk=92,
            status=NewsWorkflowRun.Status.SUCCESS,
        )
        token = self.client.get(self.url).context["coindesk_run_token"]

        response = self.client.post(
            self.url,
            {"action": "run_coindesk", "coindesk_run_token": token},
        )

        self.assertRedirects(
            response,
            "/system/schedules/runs/news/92/",
            fetch_redirect_response=False,
        )
        execute.assert_called_once_with(
            trigger=NewsWorkflowRun.Trigger.MANUAL,
            schedule=None,
            feed_group=NewsWorkflowSchedule.FeedGroup.COINDESK,
            run_ai=False,
        )


class ConcurrentNewsWorkflowTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_manual_starts_create_only_one_running_workflow(self):
        barrier = Barrier(2)

        def start():
            close_old_connections()
            barrier.wait(timeout=5)
            try:
                return _create_news_workflow_run(
                    trigger=NewsWorkflowRun.Trigger.MANUAL,
                    schedule=None,
                    started_at=FIXED_NOW,
                ).pk
            except NewsWorkflowAlreadyRunning:
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(start), pool.submit(start))]

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(
            NewsWorkflowRun.objects.filter(status=NewsWorkflowRun.Status.RUNNING).count(),
            1,
        )
