import json

import httpx
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.news_analysis.ai import DeepSeekNewsClient
from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun
from apps.news_analysis.services import AnalysisAlreadyRunning, run_news_analysis

from .helpers import ai_item, completion_payload, make_record


def mock_client(handler, *, retries=0):
    return DeepSeekNewsClient(
        base_url="https://api.deepseek.com",
        api_key="mock-key",
        model="deepseek-v4-flash",
        timeout_seconds=1,
        max_retries=retries,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def requested_ids(request):
    payload = json.loads(request.content)
    user_content = payload["messages"][1]["content"].split("\n", 1)[1]
    return [item["news_id"] for item in json.loads(user_content)["items"]]


@override_settings(
    NEWS_AI_API_KEY="mock-key",
    NEWS_AI_BATCH_SIZE=10,
    NEWS_AI_MAX_RETRIES=0,
    NEWS_AI_MAX_REQUESTS_PER_RUN=50,
    NEWS_AI_ANALYSIS_VERSION="news-v1",
    NEWS_AI_PROMPT_VERSION="prompt-v1",
)
class AnalysisServiceTests(TestCase):
    def success_client(self, item_builder=None):
        def handler(request):
            items = [
                (item_builder(news_id) if item_builder else ai_item(news_id))
                for news_id in requested_ids(request)
            ]
            return httpx.Response(200, json=completion_payload(items))

        return mock_client(handler)

    def test_four_observation_classes_are_saved(self):
        records = [make_record(title=f"Material event {index}") for index in range(4)]
        observations = ["noteworthy", "routine", "noise", "insufficient"]
        mapping = dict(zip([record.id for record in records], observations, strict=True))
        run = run_news_analysis(
            client=self.success_client(
                lambda news_id: ai_item(
                    news_id,
                    observation_result=mapping[news_id],
                    event_type="unclear" if mapping[news_id] == "insufficient" else "other",
                    impact_scope="unclear" if mapping[news_id] == "insufficient" else "crypto_market",
                )
            )
        )
        self.assertEqual(run.status, "success")
        self.assertCountEqual(
            NewsAnalysisResult.objects.values_list("observation_result", flat=True),
            observations,
        )

    def test_rule_match_saves_without_calling_ai(self):
        record = make_record(title="Join the Trading Competition")

        class NoAI:
            def analyze_batch(self, *args, **kwargs):
                raise AssertionError("AI must not be called for a fixed-rule item")

        run = run_news_analysis(client=NoAI())
        result = NewsAnalysisResult.objects.get(news_record=record)
        self.assertEqual(run.rule_processed_count, 1)
        self.assertEqual(run.api_request_count, 0)
        self.assertEqual(result.method, "rule")
        self.assertEqual(result.matched_rule_id, "binance_marketing_competition_v1")

    def test_batch_partial_failure_does_not_rollback_previous_batch(self):
        first = make_record(title="Material event one")
        second = make_record(title="Material event two")
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            news_id = requested_ids(request)[0]
            if calls == 1:
                return httpx.Response(200, json=completion_payload([ai_item(news_id)]))
            return httpx.Response(
                200,
                json={
                    "model": "deepseek-v4-flash",
                    "choices": [{"finish_reason": "stop", "message": {"content": "bad json"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                },
            )

        with override_settings(NEWS_AI_BATCH_SIZE=1):
            run = run_news_analysis(client=mock_client(handler))
        self.assertEqual(run.status, "partial")
        self.assertEqual(run.success_count, 1)
        self.assertEqual(run.failure_count, 1)
        self.assertEqual(
            NewsAnalysisResult.objects.get(news_record=first).status, "success"
        )
        self.assertEqual(
            NewsAnalysisResult.objects.get(news_record=second).status, "failed"
        )
        self.assertEqual(run.total_tokens, 127)

    def test_incremental_skips_successful_result_without_overwrite(self):
        record = make_record(title="Material event")
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json=completion_payload([ai_item(requested_ids(request)[0])]),
            )

        client = mock_client(handler)
        first_run = run_news_analysis(client=client)
        original_result = NewsAnalysisResult.objects.get(news_record=record)
        second_run = run_news_analysis(client=client)
        original_result.refresh_from_db()
        self.assertEqual(calls, 1)
        self.assertEqual(second_run.candidate_count, 0)
        self.assertEqual(original_result.analysis_run, first_run)

    def test_retry_failed_only_reprocesses_failed_record(self):
        failed_record = make_record(title="Failed event")
        successful_record = make_record(title="Successful event")
        first_calls = 0

        def first_handler(request):
            nonlocal first_calls
            first_calls += 1
            ids = requested_ids(request)
            if first_calls == 1:
                return httpx.Response(500)
            return httpx.Response(200, json=completion_payload([ai_item(ids[0])]))

        with override_settings(NEWS_AI_BATCH_SIZE=1):
            run_news_analysis(client=mock_client(first_handler))
        self.assertEqual(
            NewsAnalysisResult.objects.get(news_record=failed_record).status, "failed"
        )
        self.assertEqual(
            NewsAnalysisResult.objects.get(news_record=successful_record).status, "success"
        )
        retried_ids = []

        def retry_handler(request):
            ids = requested_ids(request)
            retried_ids.extend(ids)
            return httpx.Response(200, json=completion_payload([ai_item(ids[0])]))

        retry_run = run_news_analysis(
            mode=NewsAnalysisRun.Mode.RETRY_FAILED,
            client=mock_client(retry_handler),
        )
        self.assertEqual(retried_ids, [failed_record.id])
        self.assertEqual(retry_run.success_count, 1)
        self.assertEqual(
            NewsAnalysisResult.objects.get(news_record=failed_record).status, "success"
        )

    def test_new_analysis_version_reanalyzes_and_preserves_old_result(self):
        record = make_record(title="Versioned event")
        run_news_analysis(client=self.success_client())
        with override_settings(NEWS_AI_ANALYSIS_VERSION="news-v2"):
            run_news_analysis(client=self.success_client())
        self.assertEqual(
            NewsAnalysisResult.objects.filter(news_record=record).count(), 2
        )
        self.assertCountEqual(
            NewsAnalysisResult.objects.filter(news_record=record).values_list(
                "analysis_version", flat=True
            ),
            ["news-v1", "news-v2"],
        )

    def test_request_limit_skips_remaining_batches(self):
        for index in range(3):
            make_record(title=f"Event {index}")
        with override_settings(NEWS_AI_BATCH_SIZE=1, NEWS_AI_MAX_REQUESTS_PER_RUN=1):
            run = run_news_analysis(client=self.success_client())
        self.assertEqual(run.api_request_count, 1)
        self.assertEqual(run.success_count, 1)
        self.assertEqual(run.skipped_count, 2)
        self.assertEqual(run.status, "partial")

    def test_concurrent_running_version_is_rejected(self):
        NewsAnalysisRun.objects.create(
            trigger="manual",
            mode="incremental",
            analysis_version="news-v1",
            prompt_version="prompt-v1",
            model_name="deepseek-v4-flash",
            started_at=timezone.now(),
            status="running",
        )
        with self.assertRaises(AnalysisAlreadyRunning):
            run_news_analysis(client=self.success_client())

    def test_ai_failure_does_not_modify_raw_news_fields(self):
        record = make_record(title="Immutable title", summary="Immutable summary")
        source_status = record.source.last_inspection_status
        original_updated_at = record.updated_at
        run_news_analysis(client=mock_client(lambda request: httpx.Response(500)))
        record.refresh_from_db()
        record.source.refresh_from_db()
        self.assertEqual(record.title, "Immutable title")
        self.assertEqual(record.summary, "Immutable summary")
        self.assertEqual(record.updated_at, original_updated_at)
        self.assertEqual(record.source.last_inspection_status, source_status)
