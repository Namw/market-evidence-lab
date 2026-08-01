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
        summary="A summary",
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
    def test_batch_request_contains_only_allowed_news_fields_and_stable_parameters(self):
        record = dummy_record()
        payload = build_request_payload([record], "deepseek-v4-flash")
        sent = json.loads(payload["messages"][1]["content"].split("\n", 1)[1])["items"][0]

        self.assertEqual(
            set(sent),
            {"news_id", "source", "source_category", "published_at", "title", "summary"},
        )
        self.assertNotIn("raw_payload", payload["messages"][1]["content"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], 0.1)
        self.assertGreaterEqual(payload["max_tokens"], 1200)

    def test_valid_json_and_all_allowed_enums_are_accepted(self):
        news_id = 7
        observation_values = [value for value, _ in NewsAnalysisResult.ObservationResult.choices]
        event_values = [value for value, _ in NewsAnalysisResult.EventType.choices]
        impact_values = [value for value, _ in NewsAnalysisResult.ImpactScope.choices]
        level_values = [value for value, _ in NewsAnalysisResult.Level.choices]
        for observation in observation_values:
            validate_response_content(
                json.dumps({"items": [ai_item(news_id, observation_result=observation)]}),
                {news_id},
            )
        for event in event_values:
            validate_response_content(
                json.dumps({"items": [ai_item(news_id, event_type=event)]}), {news_id}
            )
        for impact in impact_values:
            validate_response_content(
                json.dumps({"items": [ai_item(news_id, impact_scope=impact)]}), {news_id}
            )
        for level in level_values:
            validate_response_content(
                json.dumps({"items": [ai_item(news_id, importance=level, confidence=level)]}),
                {news_id},
            )

    def test_empty_invalid_json_and_missing_items_are_rejected(self):
        for content in ("", "not-json", "{}", '{"items":{}}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                validate_response_content(content, {7})

    def test_missing_duplicate_and_extra_ids_are_rejected(self):
        cases = (
            ([ai_item(7)], {7, 8}),
            ([ai_item(7), ai_item(7)], {7}),
            ([ai_item(7), ai_item(8)], {7}),
        )
        for items, requested in cases:
            with self.subTest(items=items), self.assertRaises(ValueError):
                validate_response_content(json.dumps({"items": items}), requested)

    def test_incomplete_fields_invalid_enums_and_bad_rationale_are_rejected(self):
        invalid_items = []
        incomplete = ai_item(7)
        incomplete.pop("confidence")
        invalid_items.append(incomplete)
        for field in ("observation_result", "event_type", "impact_scope", "importance", "confidence"):
            invalid_items.append(ai_item(7, **{field: "invalid"}))
        invalid_items.extend([ai_item(7, rationale=""), ai_item(7, rationale="x" * 201)])
        for item in invalid_items:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_response_content(json.dumps({"items": [item]}), {7})

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
        self.assertEqual(result.usage.input_tokens, 11)
        self.assertEqual(result.usage.output_tokens, 7)
        self.assertEqual(result.usage.total_tokens, 18)


class AIRetryTests(SimpleTestCase):
    def test_timeout_rate_limit_and_api_error_are_safe_failures(self):
        def timeout(request):
            raise httpx.ReadTimeout("sensitive timeout detail", request=request)

        handlers = (
            timeout,
            lambda request: httpx.Response(429, json={"message": "sensitive"}),
            lambda request: httpx.Response(500, json={"message": "sensitive"}),
        )
        for handler in handlers:
            with self.subTest(handler=handler), self.assertRaises(BatchAnalysisError) as caught:
                client_for(handler).analyze_batch([dummy_record()], max_requests=1)
            self.assertNotIn("sensitive", caught.exception.safe_summary)

    def test_retry_can_succeed_and_counts_attempts(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=completion_payload([ai_item(7)]))

        result = client_for(handler, retries=2).analyze_batch([dummy_record()], max_requests=3)
        self.assertEqual(result.request_count, 2)
        self.assertEqual(result.retry_count, 1)

    def test_retry_exhaustion_counts_requests(self):
        client = client_for(lambda request: httpx.Response(500), retries=2)
        with self.assertRaises(BatchAnalysisError) as caught:
            client.analyze_batch([dummy_record()], max_requests=3)
        self.assertEqual(caught.exception.request_count, 3)
        self.assertEqual(caught.exception.retry_count, 2)

    def test_auth_and_balance_errors_are_fatal_without_retry(self):
        for status in (401, 402, 403, 404):
            with self.subTest(status=status), self.assertRaises(BatchAnalysisError) as caught:
                client_for(lambda request, status=status: httpx.Response(status), retries=2).analyze_batch(
                    [dummy_record()], max_requests=3
                )
            self.assertTrue(caught.exception.fatal)
            self.assertEqual(caught.exception.request_count, 1)
