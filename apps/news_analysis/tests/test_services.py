from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.news_analysis.ai import AIItem, BatchAnalysis, BatchAnalysisError, TokenUsage
from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun
from apps.news_analysis.services import prune_expired_news, run_news_analysis
from apps.news_data.models import NewsRawRecord
from apps.news_data.sources import COINDESK_CODE, SEC_CODE, SLOWMIST_CODE, TETHER_CODE

from .helpers import make_record


class ScriptedClient:
    def __init__(self, title="bullish", content="bearish"):
        self.title_conclusion = title
        self.content_conclusion = content
        self.calls = []

    def analyze_batch(self, records, *, max_requests, stage, contents=None):
        self.calls.append((stage, [record.id for record in records], contents))
        conclusion = (
            self.content_conclusion
            if stage in {"content_ai", "summary_ai"}
            else self.title_conclusion
        )
        items = tuple(
            AIItem(
                news_id=record.id,
                conclusion=conclusion,
                rationale="根据当前阶段输入得到明确结论。",
                content_summary="正文事件摘要。" if stage == "content_ai" else "",
            )
            for record in records
        )
        return BatchAnalysis(items, "test-model", TokenUsage(10, 5, 15), 1, 0)


class FailingClient:
    def analyze_batch(self, records, *, max_requests, stage, contents=None):
        raise BatchAnalysisError("AI 服务暂时不可用。", request_count=1)


@override_settings(
    NEWS_AI_API_KEY="mock-key",
    NEWS_AI_BATCH_SIZE=10,
    NEWS_AI_MAX_RETRIES=0,
    NEWS_AI_MAX_REQUESTS_PER_RUN=50,
    NEWS_AI_ANALYSIS_VERSION="news-eth-v2",
    NEWS_AI_PROMPT_VERSION="prompt-v2",
)
class AnalysisServiceTests(TestCase):
    article_loader = staticmethod(lambda record: "正文内容说明事件如何影响 ETH。")

    def test_program_rule_runs_first_and_irrelevant_record_is_deleted(self):
        record = make_record(title="Join the Trading Competition")

        class NoAI:
            def analyze_batch(self, *args, **kwargs):
                raise AssertionError("AI must not run after a clear program rule")

        run = run_news_analysis(client=NoAI(), article_loader=self.article_loader)
        self.assertEqual(run.rule_processed_count, 1)
        self.assertEqual(run.success_count, 1)
        self.assertFalse(NewsRawRecord.objects.filter(pk=record.pk).exists())
        self.assertFalse(NewsAnalysisResult.objects.exists())

    def test_clear_title_ai_result_stops_before_content_classification(self):
        record = make_record(title="Institution adopts Ethereum for settlement")
        client = ScriptedClient(title="bullish")
        run_news_analysis(client=client, article_loader=self.article_loader)
        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual([call[0] for call in client.calls], ["title_ai"])
        self.assertEqual(result.conclusion, "bullish")
        self.assertEqual(result.classification_stage, "title_ai")
        self.assertTrue(result.content_summary)

    def test_unclear_title_escalates_to_source_content_and_ai(self):
        record = make_record(title="Ethereum update", summary="")
        client = ScriptedClient(title="unclear", content="bearish")
        run_news_analysis(client=client, article_loader=self.article_loader)
        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual([call[0] for call in client.calls], ["title_ai", "content_ai"])
        self.assertIn("正文内容", client.calls[1][2][record.id])
        self.assertEqual(result.conclusion, "bearish")
        self.assertEqual(result.classification_stage, "content_ai")
        self.assertEqual(result.content_summary, "正文事件摘要。")

    def test_sec_unclear_title_uses_saved_rss_summary_without_article_request(self):
        record = make_record(
            source_code=SEC_CODE,
            title="SEC issues regulatory update",
            summary="The SEC update concerns Ethereum market access.",
        )
        client = ScriptedClient(title="unclear", content="bearish")

        def forbidden_loader(_record):
            raise AssertionError("SEC V1 must not request article content")

        run_news_analysis(client=client, article_loader=forbidden_loader)

        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual(
            [call[0] for call in client.calls], ["title_ai", "summary_ai"]
        )
        self.assertEqual(
            client.calls[1][2][record.id],
            "The SEC update concerns Ethereum market access.",
        )
        self.assertEqual(result.classification_stage, "summary_ai")

    def test_tether_unclear_title_uses_saved_api_excerpt_without_article_request(self):
        record = make_record(
            source_code=TETHER_CODE,
            title="Tether publishes an update",
            summary="The official API excerpt mentions Ethereum settlement.",
        )
        client = ScriptedClient(title="unclear", content="bullish")

        def forbidden_loader(_record):
            raise AssertionError("Tether collection must not request article content")

        run_news_analysis(client=client, article_loader=forbidden_loader)

        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual([call[0] for call in client.calls], ["title_ai", "summary_ai"])
        self.assertEqual(
            client.calls[1][2][record.id],
            "The official API excerpt mentions Ethereum settlement.",
        )
        self.assertEqual(result.classification_stage, "summary_ai")

    def test_coindesk_unclear_title_uses_saved_rss_summary(self):
        record = make_record(
            source_code=COINDESK_CODE,
            title="CoinDesk publishes a market update",
            summary="The RSS summary describes the market event and its ETH impact.",
        )
        client = ScriptedClient(title="unclear", content="bearish")

        def forbidden_loader(_record):
            raise AssertionError("CoinDesk classification must not request article content")

        run_news_analysis(client=client, article_loader=forbidden_loader)

        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual([call[0] for call in client.calls], ["title_ai", "summary_ai"])
        self.assertEqual(
            client.calls[1][2][record.id],
            "The RSS summary describes the market event and its ETH impact.",
        )
        self.assertEqual(result.classification_stage, "summary_ai")

    def test_slowmist_unclear_title_uses_saved_event_description(self):
        record = make_record(
            source_code=SLOWMIST_CODE,
            title="Hacked target: Ethereum Bridge",
            summary="An Ethereum bridge security event was recorded by SlowMist.",
        )
        client = ScriptedClient(title="unclear", content="bearish")

        def forbidden_loader(_record):
            raise AssertionError("SlowMist references must not be fetched")

        run_news_analysis(client=client, article_loader=forbidden_loader)

        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual([call[0] for call in client.calls], ["title_ai", "summary_ai"])
        self.assertEqual(
            client.calls[1][2][record.id],
            "An Ethereum bridge security event was recorded by SlowMist.",
        )
        self.assertEqual(result.classification_stage, "summary_ai")

    def test_ai_irrelevant_result_deletes_raw_record(self):
        record = make_record(title="Other token reward event")
        run_news_analysis(
            client=ScriptedClient(title="irrelevant"),
            article_loader=self.article_loader,
        )
        self.assertFalse(NewsRawRecord.objects.filter(pk=record.pk).exists())

    def test_general_source_unclear_is_deleted_after_three_days(self):
        record = make_record(title="Ambiguous Ethereum item")
        record.source.authority_level = "general"
        record.source.save(update_fields=["authority_level"])
        run_news_analysis(
            client=ScriptedClient(title="unclear", content="unclear"),
            article_loader=self.article_loader,
        )
        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual(result.conclusion, "unclear")
        result.analyzed_at = timezone.now() - timedelta(days=3, seconds=1)
        result.save(update_fields=["analyzed_at"])
        self.assertGreater(prune_expired_news(), 0)
        self.assertFalse(NewsRawRecord.objects.filter(pk=record.pk).exists())

    def test_highest_and_medium_source_unclear_are_retained_after_three_days(self):
        for source_code in ("binance_announcements", SEC_CODE):
            with self.subTest(source_code=source_code):
                record = make_record(
                    source_code=source_code,
                    title=f"Ambiguous Ethereum item from {source_code}",
                )
                run_news_analysis(
                    client=ScriptedClient(title="unclear", content="unclear"),
                    article_loader=self.article_loader,
                )
                result = NewsAnalysisResult.objects.get(news_record=record)
                result.analyzed_at = timezone.now() - timedelta(days=30)
                result.save(update_fields=["analyzed_at"])

                prune_expired_news(now=timezone.now())

                self.assertTrue(
                    NewsRawRecord.objects.filter(pk=record.pk).exists()
                )

    def test_recent_unclear_survives_cleanup(self):
        record = make_record(title="Ambiguous Ethereum item")
        run_news_analysis(
            client=ScriptedClient(title="unclear", content="unclear"),
            article_loader=self.article_loader,
        )
        prune_expired_news(now=timezone.now())
        self.assertTrue(NewsRawRecord.objects.filter(pk=record.pk).exists())

    def test_incremental_does_not_overwrite_successful_result(self):
        record = make_record(title="Material ETH event")
        client = ScriptedClient(title="bullish")
        first_run = run_news_analysis(client=client, article_loader=self.article_loader)
        second_run = run_news_analysis(client=client, article_loader=self.article_loader)
        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual(second_run.candidate_count, 0)
        self.assertEqual(result.analysis_run, first_run)

    def test_failure_is_saved_for_retry(self):
        record = make_record(title="Material ETH event")
        run = run_news_analysis(client=FailingClient(), article_loader=self.article_loader)
        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual(run.status, NewsAnalysisRun.Status.FAILED)
        self.assertEqual(result.status, NewsAnalysisResult.Status.FAILED)
        self.assertFalse(result.conclusion)

    @override_settings(NEWS_AI_API_KEY="")
    def test_missing_api_key_leaves_candidates_unclassified(self):
        make_record(title="Material ETH event")
        run = run_news_analysis(article_loader=self.article_loader)
        self.assertEqual(run.status, NewsAnalysisRun.Status.NOT_RUN)
        self.assertFalse(NewsAnalysisResult.objects.exists())
