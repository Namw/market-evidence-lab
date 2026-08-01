from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun

from .helpers import make_record


def make_run(**overrides):
    values = {
        "trigger": "manual",
        "mode": "incremental",
        "analysis_version": "news-v1",
        "prompt_version": "prompt-v1",
        "model_name": "deepseek-v4-flash",
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
        "status": "success",
    }
    values.update(overrides)
    return NewsAnalysisRun.objects.create(**values)


def make_result(record, **overrides):
    run = overrides.pop("analysis_run", None) or make_run()
    values = {
        "news_record": record,
        "analysis_version": "news-v1",
        "prompt_version": "prompt-v1",
        "status": "success",
        "observation_result": "noteworthy",
        "event_type": "security_incident",
        "impact_scope": "crypto_market",
        "importance": "high",
        "rationale": "重大安全事件值得后续观察。",
        "confidence": "high",
        "method": "ai",
        "actual_model_name": "deepseek-v4-flash",
        "analysis_run": run,
        "analyzed_at": timezone.now(),
    }
    values.update(overrides)
    return NewsAnalysisResult.objects.create(**values)


@override_settings(
    NEWS_AI_ANALYSIS_VERSION="news-v1",
    NEWS_AI_PROMPT_VERSION="prompt-v1",
    NEWS_AI_MODEL="deepseek-v4-flash",
)
class NewsObservationViewTests(TestCase):
    def test_page_works_without_key_and_shows_configuration_and_counts(self):
        noteworthy = make_record(title="Security incident")
        make_result(noteworthy)
        make_record(title="Not analyzed")

        with override_settings(NEWS_AI_API_KEY=""):
            response = self.client.get(reverse("news_analysis:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "news_analysis/index.html")
        self.assertContains(response, "未配置")
        self.assertContains(response, "新闻观察")
        self.assertEqual(response.context["raw_total"], 2)
        self.assertEqual(response.context["success_count"], 1)
        self.assertEqual(response.context["unanalyzed_count"], 1)

    def test_page_lists_results_and_supports_all_filter_dimensions(self):
        record = make_record(title="Ethereum protocol upgrade", category="Updates")
        result = make_result(
            record,
            event_type="protocol_upgrade",
            impact_scope="ethereum",
            importance="high",
            confidence="medium",
            method="ai",
        )
        filter_query = {
            "source": record.source_id,
            "observation_result": "noteworthy",
            "event_type": "protocol_upgrade",
            "impact_scope": "ethereum",
            "importance": "high",
            "confidence": "medium",
            "method": "ai",
            "status": "success",
        }
        response = self.client.get(reverse("news_analysis:index"), filter_query)
        self.assertContains(response, record.title)
        self.assertContains(response, "协议升级")
        self.assertContains(response, "以太坊")
        self.assertIn(result, list(response.context["page"].object_list))

        filter_query["event_type"] = "security_incident"
        response = self.client.get(reverse("news_analysis:index"), filter_query)
        self.assertNotContains(response, record.title)

    def test_failed_result_shows_only_safe_error_summary(self):
        record = make_record(title="Failed item")
        run = make_run(status="failed", safe_error_summary="AI 服务暂时不可用。")
        make_result(
            record,
            analysis_run=run,
            status="failed",
            observation_result="",
            event_type="",
            impact_scope="",
            importance="",
            rationale="",
            confidence="",
            method="",
            actual_model_name="",
            safe_error_summary="AI 服务暂时不可用。",
        )
        response = self.client.get(reverse("news_analysis:index"))
        self.assertContains(response, "AI 服务暂时不可用。")

    @override_settings(NEWS_AI_API_KEY="configured-for-test")
    @patch("apps.news_analysis.views.run_news_analysis")
    def test_post_triggers_incremental_and_retry_modes(self, run_analysis):
        run_analysis.return_value = SimpleNamespace(
            status=NewsAnalysisRun.Status.SUCCESS
        )
        for mode in ("incremental", "retry_failed"):
            with self.subTest(mode=mode):
                response = self.client.post(
                    reverse("news_analysis:run", args=[mode])
                )
                self.assertRedirects(response, reverse("news_analysis:index"))
        self.assertEqual(
            [call.kwargs["mode"] for call in run_analysis.call_args_list],
            ["incremental", "retry_failed"],
        )

    @override_settings(NEWS_AI_API_KEY="configured-for-test")
    def test_post_keeps_csrf_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("news_analysis:run", args=["incremental"])
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(NEWS_AI_API_KEY="")
    @patch("apps.news_analysis.views.run_news_analysis")
    def test_missing_key_prevents_run_without_exposing_value(self, run_analysis):
        response = self.client.post(
            reverse("news_analysis:run", args=["incremental"]), follow=True
        )
        run_analysis.assert_not_called()
        self.assertContains(response, "DeepSeek API 未配置")

    def test_navigation_places_daily_results_under_news_observation(self):
        response = self.client.get(reverse("news_analysis:index"))
        self.assertContains(response, 'aria-label="新闻观察"')
        self.assertContains(
            response,
            '<details class="nav-group is-active" data-nav-group="news" open>',
        )
        self.assertContains(response, 'href="/analysis/news/" aria-current="page"')
