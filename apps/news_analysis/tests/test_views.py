from types import SimpleNamespace
from unittest.mock import patch
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.news_analysis.content import ArticleContent, SourceContentError
from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun

from .helpers import make_record
from apps.news_data.sources import SEC_CODE


def make_run(**overrides):
    values = {
        "trigger": "manual",
        "mode": "incremental",
        "analysis_version": "news-eth-v2",
        "prompt_version": "prompt-v2",
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
        "analysis_version": "news-eth-v2",
        "prompt_version": "prompt-v2",
        "status": "success",
        "conclusion": "bullish",
        "classification_stage": "title_ai",
        "rationale": "ETH 的采用入口明确增加。",
        "content_summary": "正文说明机构采用 ETH。",
        "method": "ai",
        "actual_model_name": "deepseek-v4-flash",
        "analysis_run": run,
        "analyzed_at": timezone.now(),
    }
    values.update(overrides)
    return NewsAnalysisResult.objects.create(**values)


@override_settings(
    NEWS_AI_ANALYSIS_VERSION="news-eth-v2",
    NEWS_AI_PROMPT_VERSION="prompt-v2",
    NEWS_AI_MODEL="deepseek-v4-flash",
)
class NewsClassificationViewTests(TestCase):
    def test_page_is_recent_classification_list_with_requested_navigation(self):
        record = make_record(title="Ethereum adoption event")
        make_result(record)
        response = self.client.get(reverse("news_analysis:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "数据分类结果")
        self.assertContains(response, record.title)
        self.assertContains(response, "利好")
        navigation = response.content.decode().split('<nav class="navigation">', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(navigation.count('class="nav-subitem'), 11)
        self.assertIn("新闻事件库", navigation)
        self.assertIn("客观事实提取", navigation)
        self.assertIn("每日分析结果", navigation)
        self.assertIn("数据采集", navigation)

    def test_filters_by_source_conclusion_and_stage(self):
        record = make_record(title="Ethereum protocol event")
        result = make_result(record, conclusion="bearish", classification_stage="content_ai")
        response = self.client.get(
            reverse("news_analysis:index"),
            {
                "source": record.source_id,
                "conclusion": "bearish",
                "classification_stage": "content_ai",
            },
        )
        self.assertIn(result, list(response.context["page"].object_list))
        response = self.client.get(
            reverse("news_analysis:index"), {"conclusion": "bullish"}
        )
        self.assertNotContains(response, record.title)

    def test_displays_and_filters_source_authority_level(self):
        highest_record = make_record(
            source_code=SEC_CODE, title="SEC authority filter result"
        )
        medium_record = make_record(title="Binance authority filter result")
        highest_result = make_result(highest_record)
        make_result(medium_record)

        response = self.client.get(
            reverse("news_analysis:index"), {"authority_level": "highest"}
        )

        self.assertIn(highest_result, list(response.context["page"].object_list))
        self.assertContains(response, "权威：最高")
        self.assertContains(response, highest_record.title)
        self.assertNotContains(response, medium_record.title)

    def test_custom_classification_time_range_overrides_recent_three_days(self):
        record = make_record(title="Older retained ETH classification")
        analyzed_at = timezone.now() - timedelta(days=5)
        result = make_result(record, analyzed_at=analyzed_at)

        default_response = self.client.get(reverse("news_analysis:index"))
        self.assertNotContains(default_response, record.title)

        start = timezone.localtime(analyzed_at - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        end = timezone.localtime(analyzed_at + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        response = self.client.get(
            reverse("news_analysis:index"),
            {"start_time": start, "end_time": end},
        )
        self.assertIn(result, list(response.context["page"].object_list))
        self.assertEqual(response.context["range_label"], "自定义分类时间")
        self.assertIn("start_time=", response.context["pagination_query"])

    def test_invalid_classification_time_range_shows_error(self):
        response = self.client.get(
            reverse("news_analysis:index"),
            {
                "start_time": "2026-08-03T10:00",
                "end_time": "2026-08-02T10:00",
            },
        )
        self.assertContains(response, "分类开始时间不能晚于结束时间")

    @patch("apps.news_analysis.views.fetch_source_article")
    def test_detail_endpoint_prefers_live_source_content(self, fetch):
        result = make_result(make_record(title="Ethereum event"))
        fetch.return_value = ArticleContent(
            text="Source article body with enough details to classify ETH.",
            source_url="https://www.binance.com/source",
        )
        response = self.client.get(
            reverse("news_analysis:result_content", args=[result.id])
        )
        self.assertEqual(response.json()["origin"], "source")
        self.assertIn("Source article body", response.json()["content"])

    @patch("apps.news_analysis.views.fetch_source_article")
    def test_detail_endpoint_falls_back_to_saved_summary_and_keeps_url(self, fetch):
        record = make_record(title="Ethereum event")
        result = make_result(record, content_summary="已保存的正文摘要。")
        fetch.side_effect = SourceContentError("unavailable")
        response = self.client.get(
            reverse("news_analysis:result_content", args=[result.id])
        )
        payload = response.json()
        self.assertEqual(payload["origin"], "saved_summary")
        self.assertEqual(payload["content"], "已保存的正文摘要。")
        self.assertEqual(payload["source_url"], record.original_url)

    @patch("apps.news_analysis.views.fetch_source_article")
    def test_sec_detail_never_requests_article_content(self, fetch):
        record = make_record(
            source_code=SEC_CODE,
            title="SEC Ethereum update",
            summary="Saved RSS summary.",
        )
        result = make_result(record, content_summary="已保存的 RSS 摘要。")

        response = self.client.get(
            reverse("news_analysis:result_content", args=[result.id])
        )

        fetch.assert_not_called()
        self.assertEqual(response.json()["origin"], "saved_summary")

    @override_settings(NEWS_AI_API_KEY="configured-for-test")
    @patch("apps.news_analysis.views.run_news_analysis")
    def test_post_triggers_incremental_mode(self, run_analysis):
        run_analysis.return_value = SimpleNamespace(status=NewsAnalysisRun.Status.SUCCESS)
        response = self.client.post(
            reverse("news_analysis:run", args=["incremental"])
        )
        self.assertRedirects(response, reverse("news_analysis:index"))
        self.assertEqual(run_analysis.call_args.kwargs["mode"], "incremental")

    @override_settings(NEWS_AI_API_KEY="configured-for-test")
    def test_post_keeps_csrf_protection(self):
        response = Client(enforce_csrf_checks=True).post(
            reverse("news_analysis:run", args=["incremental"])
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(NEWS_AI_API_KEY="")
    @patch("apps.news_analysis.views.run_news_analysis")
    def test_missing_key_prevents_run(self, run_analysis):
        response = self.client.post(
            reverse("news_analysis:run", args=["incremental"]), follow=True
        )
        run_analysis.assert_not_called()
        self.assertContains(response, "DeepSeek API 未配置")
