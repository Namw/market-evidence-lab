import json
from datetime import UTC, datetime
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.news_analysis.fact_validation import (
    FACT_SYSTEM_PROMPT,
    FILTER_SYSTEM_PROMPT,
    build_filter_request_payload,
    database_article_input,
    validate_fact_response,
    validate_filter_response,
)


def filter_item(**overrides):
    item = {
        "news_id": 7,
        "research_relevance": "direct",
        "content_type": "concrete_event",
        "filter_reason_code": "PASS_DIRECT_EVENT",
        "filter_reason": "输入明确报告了与 ETH 直接相关的具体事件。",
        "should_extract_event": True,
    }
    item.update(overrides)
    return item


def fact_item(**overrides):
    item = {
        "news_id": 7,
        "content_type": "concrete_event",
        "contains_concrete_event": True,
        "event_subjects": ["主体"],
        "event_action": "发布",
        "event_objects": ["对象"],
        "event_occurred_at": None,
        "event_time_basis": "published_time_only",
        "key_facts": ["主体发布了对象。"],
        "involved_scopes": ["ETH"],
        "objective_summary": "主体发布了与 ETH 相关的对象。",
    }
    item.update(overrides)
    return item


def article_input():
    return SimpleNamespace(
        news_id=7,
        title="主体发布对象",
        summary="数据库摘要没有事件日期。",
        stored_body="",
        published_at="2026-08-01T20:10:00+00:00",
    )


class DatabaseInputTests(SimpleTestCase):
    def test_only_approved_database_text_fields_are_sent(self):
        record = SimpleNamespace(
            id=7,
            title="A title",
            summary="Saved summary",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
            raw_payload={
                "article_text": "Saved article body",
                "secret": "must not be sent",
                "link": "https://example.com/not-fetched",
            },
        )
        article = database_article_input(record)
        payload, _ = build_filter_request_payload([article], "test-model")
        prompt = payload["messages"][1]["content"]
        self.assertIn("Saved article body", prompt)
        self.assertNotIn("must not be sent", prompt)
        self.assertNotIn("example.com", prompt)

    def test_prompts_forbid_source_defaults_cross_news_context_and_network(self):
        self.assertIn("每篇新闻必须独立判断", FILTER_SYSTEM_PROMPT)
        self.assertIn("source 不会提供给你", FILTER_SYSTEM_PROMPT)
        self.assertIn("不得联网", FILTER_SYSTEM_PROMPT)
        self.assertIn("不等于适合解释 ETH 日K异常", FILTER_SYSTEM_PROMPT)
        self.assertIn("不得自行创造", FILTER_SYSTEM_PROMPT)
        self.assertIn("项目名称含 Ethereum", FILTER_SYSTEM_PROMPT)
        self.assertIn("每篇新闻独立处理", FACT_SYSTEM_PROMPT)
        self.assertIn("不得用同批其他新闻", FACT_SYSTEM_PROMPT)


class FilterResponseTests(SimpleTestCase):
    def test_valid_pass_and_valid_filtered_item(self):
        payload = {
            "items": [
                filter_item(),
                filter_item(
                    news_id=8,
                    research_relevance="uncertain",
                    content_type="unknown",
                    filter_reason_code="INSUFFICIENT_INFORMATION",
                    filter_reason="现有标题和摘要不足以确认系统性渠道。",
                    should_extract_event=False,
                ),
            ]
        }
        result = validate_filter_response(
            json.dumps(payload, ensure_ascii=False), {7, 8}
        )
        self.assertTrue(result[0].should_extract_event)
        self.assertFalse(result[1].should_extract_event)

    def test_uncertain_or_irrelevant_cannot_enter_extraction(self):
        for relevance in ("uncertain", "irrelevant"):
            with self.subTest(relevance=relevance), self.assertRaises(ValueError):
                validate_filter_response(
                    json.dumps(
                        {"items": [filter_item(research_relevance=relevance)]},
                        ensure_ascii=False,
                    ),
                    {7},
                )

    def test_pass_code_boolean_and_relevance_must_be_consistent(self):
        invalid_items = (
            filter_item(should_extract_event=False),
            filter_item(filter_reason_code="PASS_BROAD_MARKET_EVENT"),
            filter_item(
                filter_reason_code="NO_CONCRETE_EVENT",
                should_extract_event=True,
            ),
        )
        for item in invalid_items:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_filter_response(json.dumps({"items": [item]}), {7})


class FactResponseTests(SimpleTestCase):
    def test_valid_fact_result_is_accepted(self):
        result = validate_fact_response(
            json.dumps({"items": [fact_item()]}, ensure_ascii=False),
            {7},
            {7: article_input()},
        )
        self.assertTrue(result[0].contains_concrete_event)

    def test_second_stage_rejects_no_event(self):
        with self.assertRaisesRegex(ValueError, "必须包含具体事件"):
            validate_fact_response(
                json.dumps(
                    {"items": [fact_item(contains_concrete_event=False)]},
                    ensure_ascii=False,
                ),
                {7},
                {7: article_input()},
            )

    def test_unsupported_publication_date_is_not_event_date(self):
        result = validate_fact_response(
            json.dumps(
                {
                    "items": [
                        fact_item(
                            event_occurred_at="2026-08-01",
                            event_time_basis="explicit",
                        )
                    ]
                },
                ensure_ascii=False,
            ),
            {7},
            {7: article_input()},
        )
        self.assertIsNone(result[0].event_occurred_at)
        self.assertEqual(result[0].event_time_basis, "published_time_only")

    def test_today_supports_date_inference_from_publication_date(self):
        article = article_input()
        article.summary = "主体 today announced the event."
        result = validate_fact_response(
            json.dumps(
                {
                    "items": [
                        fact_item(
                            event_occurred_at="2026-08-01",
                            event_time_basis="explicit",
                        )
                    ]
                },
                ensure_ascii=False,
            ),
            {7},
            {7: article},
        )
        self.assertEqual(result[0].event_occurred_at, "2026-08-01")
        self.assertEqual(result[0].event_time_basis, "inferred")

    def test_ids_and_enums_are_strict(self):
        with self.assertRaises(ValueError):
            validate_fact_response(
                json.dumps({"items": [fact_item(news_id=8)]}),
                {7},
                {7: article_input()},
            )
        with self.assertRaises(ValueError):
            validate_fact_response(
                json.dumps({"items": [fact_item(involved_scopes=["DOGE"])]}),
                {7},
                {7: article_input()},
            )
