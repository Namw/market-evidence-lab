import json
from types import SimpleNamespace

import httpx
from django.test import SimpleTestCase, override_settings

from apps.news_analysis.objective_fact_validation import (
    DeepSeekObjectiveFactClient,
    ObjectiveArticleInput,
    SYSTEM_PROMPT,
    build_request_payload,
    match_evidence,
    validate_parsed_result,
    valid_time_value,
)
from apps.news_analysis.models import ObjectiveFactExtractionResult
from apps.news_analysis.objective_fact_schema import (
    EVENT_STATUS_CHOICES,
    EVENT_STATUS_PROMPT_TEXT,
    EVENT_STATUS_VALUES,
)
from apps.news_analysis.objective_fact_validation import EVENT_STATUSES


def article(**overrides):
    values = {
        "news_id": 106,
        "source": {"code": "example", "name": "Example"},
        "title": "Company receives approval",
        "summary": "On July 31, 2026, Company said revenue was $1.5B.",
        "stored_body": "The company plans to stop Product A.",
        "published_at": "2026-08-01T00:00:00+00:00",
        "source_category": "News",
        "source_tags": ["Company"],
        "source_author": "Reporter",
    }
    values.update(overrides)
    return ObjectiveArticleInput(**values)


def result(**overrides):
    values = {
        "event_title": "Company reports revenue",
        "event_time": "2026-07-31",
        "actors": ["Company"],
        "action": "reported revenue",
        "object": ["revenue"],
        "event_status": "announced",
        "facts": [
            {
                "statement": "Company said revenue was $1.5B.",
                "claim_type": "company_claim",
                "evidence_text": "On July 31, 2026, Company said revenue was $1.5B.",
                "fact_time": "2026-07-31",
                "amounts": [{"text": "$1.5B", "kind": "money"}],
            }
        ],
        "objective_summary": "Company said revenue was $1.5B.",
        "information_completeness": "sufficient",
    }
    values.update(overrides)
    return values


class PromptAndEvidenceTests(SimpleTestCase):
    def test_prompt_contains_required_boundaries_and_no_secret(self):
        self.assertIn("不得联网", SYSTEM_PROMPT)
        self.assertIn("不得与相似新闻合并", SYSTEM_PROMPT)
        self.assertIn("自行加入 ETH", SYSTEM_PROMPT)
        self.assertIn("公司自述", SYSTEM_PROMPT)
        self.assertIn("published_at 只是新闻发布时间", SYSTEM_PROMPT)
        payload, prompt = build_request_payload(article(), "deepseek-test")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertIn("Company receives approval", prompt)
        self.assertNotIn("api_key", json.dumps(payload))

    def test_event_status_has_one_source_for_prompt_validator_and_model(self):
        self.assertIn(
            f"event_status 只能是 {EVENT_STATUS_PROMPT_TEXT}", SYSTEM_PROMPT
        )
        self.assertEqual(EVENT_STATUSES, frozenset(EVENT_STATUS_VALUES))
        self.assertEqual(
            tuple(ObjectiveFactExtractionResult._meta.get_field("event_status").choices),
            EVENT_STATUS_CHOICES,
        )
        self.assertNotIn(("reported", "reported"), EVENT_STATUS_CHOICES)

    def test_prompt_distinguishes_title_fact_from_opinion_and_requires_dedup(self):
        self.assertIn("title、summary、stored_body 都是正式事实输入", SYSTEM_PROMPT)
        self.assertIn("不得仅因“只有标题”就自动判定 insufficient", SYSTEM_PROMPT)
        self.assertIn("观点、问题、修辞", SYSTEM_PROMPT)
        self.assertIn("只保留一条 fact", SYSTEM_PROMPT)

    def test_evidence_matching_only_normalizes_whitespace(self):
        matched = match_evidence("Company said   revenue was $1.5B.", article())
        self.assertTrue(matched["matched"])
        self.assertEqual(matched["matched_field"], "summary")
        changed_case = match_evidence("company said revenue was $1.5B.", article())
        self.assertFalse(changed_case["matched"])


class StructureValidationTests(SimpleTestCase):
    def test_valid_result_passes(self):
        validation = validate_parsed_result(result(), article())
        self.assertEqual(validation["errors"], [])
        self.assertTrue(validation["evidence_matches"][0]["matched"])

    def test_invalid_enum_evidence_amount_and_time_are_separate_errors(self):
        raw = result(
            event_status="done",
            event_time="08/01/2026",
            facts=[
                {
                    "statement": "Invented.",
                    "claim_type": "fact",
                    "evidence_text": "Invented evidence.",
                    "fact_time": "tomorrow",
                    "amounts": [{"text": "$9B", "kind": "currency"}],
                }
            ],
        )
        validation = validate_parsed_result(raw, article())
        codes = {error["code"] for error in validation["errors"]}
        self.assertTrue(
            {
                "INVALID_EVENT_STATUS",
                "INVALID_EVENT_TIME",
                "INVALID_CLAIM_TYPE",
                "EVIDENCE_NOT_MATCHED",
                "INVALID_FACT_TIME",
                "AMOUNT_NOT_IN_EVIDENCE",
                "INVALID_AMOUNT_KIND",
            }.issubset(codes)
        )

    def test_unsupported_eth_reference_is_detected(self):
        raw = result(event_title="ETH event")
        validation = validate_parsed_result(raw, article())
        self.assertIn(
            "UNSUPPORTED_ETH_REFERENCE",
            {error["code"] for error in validation["errors"]},
        )

    def test_insufficient_cannot_force_facts_or_summary(self):
        raw = result(information_completeness="insufficient")
        validation = validate_parsed_result(raw, article())
        self.assertIn(
            "INSUFFICIENT_WITH_CONCRETE_OUTPUT",
            {error["code"] for error in validation["errors"]},
        )

    def test_time_formats(self):
        for value in ("2026", "2026-Q2", "2026-07", "2026-07-31", "2026-07-31T12:00:00Z"):
            self.assertTrue(valid_time_value(value))
        for value in ("today", "2026-13", "07/31/2026"):
            self.assertFalse(valid_time_value(value))


class ClientFailureClassificationTests(SimpleTestCase):
    def make_ai_client(self, handler):
        return DeepSeekObjectiveFactClient(
            http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    @override_settings(NEWS_AI_API_KEY="test-key", NEWS_AI_MODEL="deepseek-test")
    def test_json_error_keeps_raw_output(self):
        client = self.make_ai_client(
            lambda request: httpx.Response(
                200,
                json={
                    "model": "deepseek-test",
                    "choices": [{"message": {"content": "not json"}}],
                },
            )
        )
        item = client.process(article())
        self.assertEqual(item["processing_status"], "json_parse_failed")
        self.assertEqual(item["raw_model_output"], "not json")

    @override_settings(NEWS_AI_API_KEY="test-key", NEWS_AI_MODEL="deepseek-test")
    def test_http_error_is_ai_call_failure(self):
        client = self.make_ai_client(
            lambda request: httpx.Response(429, json={"error": "rate limit"})
        )
        item = client.process(article())
        self.assertEqual(item["processing_status"], "ai_call_failed")
        self.assertEqual(item["api_response"], {"error": "rate limit"})
