import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from django.test import SimpleTestCase

from apps.news_analysis.ai import (
    BatchAnalysisError,
    DeepSeekNewsClient,
    build_request_payload,
    validate_response_content,
)
from apps.news_analysis.models import NewsAnalysisResult

from .helpers import ai_item, completion_payload


def dummy_record(news_id=7):
    return SimpleNamespace(
        id=news_id,
        source=SimpleNamespace(code="binance_announcements"),
        source_category="Latest",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        title="A title",
        summary="A saved summary",
        raw_payload={"secret": "not sent"},
    )


def client_for(handler, *, retries=0):
    return DeepSeekNewsClient(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-v4-flash",
        timeout_seconds=1,
        max_retries=retries,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class AIRequestTests(SimpleTestCase):
    def test_title_stage_sends_title_without_summary_or_raw_payload(self):
        payload = build_request_payload([dummy_record()], "deepseek-v4-flash")
        content = payload["messages"][1]["content"]
        sent = json.loads(content.rsplit("\n待分类新闻：\n", 1)[1])["items"][0]
        self.assertEqual(
            set(sent), {"news_id", "source", "source_category", "published_at", "title"}
        )
        self.assertNotIn("A saved summary", content)
        self.assertNotIn("secret", content)

    def test_content_stage_adds_only_supplied_article_content(self):
        record = dummy_record()
        payload = build_request_payload(
            [record],
            "deepseek-v4-flash",
            stage=NewsAnalysisResult.ClassificationStage.CONTENT_AI,
            contents={record.id: "source article body"},
        )
        self.assertIn("source article body", payload["messages"][1]["content"])

    def test_all_four_conclusions_are_validated(self):
        for conclusion, _ in NewsAnalysisResult.Conclusion.choices:
            result = validate_response_content(
                json.dumps({"items": [ai_item(7, conclusion=conclusion)]}), {7}
            )
            self.assertEqual(result[0].conclusion, conclusion)

    def test_invalid_schema_ids_and_lengths_are_rejected(self):
        invalid = ai_item(7)
        invalid.pop("content_summary")
        cases = (
            {"items": [invalid]},
            {"items": [ai_item(7), ai_item(7)]},
            {"items": [ai_item(8)]},
            {"items": [ai_item(7, conclusion="maybe")]},
            {"items": [ai_item(7, rationale="x" * 201)]},
            {"items": [ai_item(7, content_summary="x" * 601)]},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_response_content(json.dumps(payload), {7})

    def test_finish_reason_length_is_failure(self):
        client = client_for(
            lambda request: httpx.Response(
                200, json=completion_payload([ai_item(7)], finish_reason="length")
            )
        )
        with self.assertRaises(BatchAnalysisError) as caught:
            client.analyze_batch([dummy_record()], max_requests=1)
        self.assertIn("截断", caught.exception.safe_summary)

    def test_token_usage_and_actual_model_are_returned(self):
        client = client_for(
            lambda request: httpx.Response(
                200,
                json=completion_payload(
                    [ai_item(7)],
                    model="deepseek-v4-flash-actual",
                    usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                ),
            )
        )
        result = client.analyze_batch([dummy_record()], max_requests=1)
        self.assertEqual(result.actual_model_name, "deepseek-v4-flash-actual")
        self.assertEqual(result.usage.total_tokens, 18)


class AIRetryTests(SimpleTestCase):
    def test_retry_can_succeed_and_counts_attempts(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=completion_payload([ai_item(7)]))

        result = client_for(handler, retries=2).analyze_batch(
            [dummy_record()], max_requests=3
        )
        self.assertEqual(result.request_count, 2)
        self.assertEqual(result.retry_count, 1)

    def test_auth_error_is_fatal_without_retry(self):
        with self.assertRaises(BatchAnalysisError) as caught:
            client_for(lambda request: httpx.Response(401), retries=2).analyze_batch(
                [dummy_record()], max_requests=3
            )
        self.assertTrue(caught.exception.fatal)
        self.assertEqual(caught.exception.request_count, 1)
