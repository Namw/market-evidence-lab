import json

import httpx
from django.test import TestCase, override_settings

from apps.news_analysis.models import (
    ObjectiveFactExtractionResult,
    ObjectiveFactExtractionRun,
)
from apps.news_analysis.objective_fact_validation import validate_parsed_result
from apps.news_analysis.objective_fact_schema import EVENT_STATUS_VALUES
from apps.news_analysis.objective_fact_validation import DeepSeekObjectiveFactClient
from apps.news_analysis.objective_facts import run_objective_fact_extraction

from .helpers import make_record


def valid_parsed(summary="Company announced Product A."):
    return {
        "event_title": "Company announces Product A",
        "event_time": None,
        "actors": ["Company"],
        "action": "announced",
        "object": ["Product A"],
        "event_status": "announced",
        "facts": [
            {
                "statement": "Company announced Product A.",
                "claim_type": "company_claim",
                "evidence_text": summary,
                "fact_time": None,
                "amounts": [],
            }
        ],
        "objective_summary": "Company announced Product A.",
        "information_completeness": "sufficient",
    }


class FakeClient:
    def __init__(self, factory):
        self.factory = factory
        self.calls = []

    def process(self, article):
        self.calls.append(article.news_id)
        value = self.factory(article)
        if isinstance(value, Exception):
            raise value
        return value


def full_chain_client(parsed_results):
    pending = list(parsed_results)

    def handler(request):
        parsed = pending.pop(0)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-test",
                "choices": [
                    {"message": {"content": json.dumps(parsed, ensure_ascii=False)}}
                ],
            },
        )

    return DeepSeekObjectiveFactClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def processed_success(article, *, parsed=None, validation=None):
    parsed = parsed or valid_parsed(article.summary)
    return {
        "processing_status": "validation_failed"
        if validation and validation.get("errors")
        else "success",
        "api_response": {"model": "deepseek-returned"},
        "raw_model_output": "raw-json",
        "parsed_result": parsed,
        "json_parse_error": None,
        "validation": validation or validate_parsed_result(parsed, article),
    }


@override_settings(NEWS_OBJECTIVE_FACT_PROMPT_VERSION="objective-news-facts-v1")
class ObjectiveFactServiceTests(TestCase):
    def test_valid_result_saves_complete_audit_and_run_counts(self):
        record = make_record(summary="Company announced Product A.")
        client = FakeClient(processed_success)

        run = run_objective_fact_extraction(client=client)

        result = ObjectiveFactExtractionResult.objects.get()
        self.assertEqual(result.extraction_status, "success")
        self.assertEqual(result.validation_status, "passed")
        self.assertTrue(result.ai_call_succeeded)
        self.assertTrue(result.json_parse_succeeded)
        self.assertEqual(result.model, "deepseek-returned")
        self.assertEqual(result.input_snapshot["title"], record.title)
        self.assertIn("绝对规则", result.system_prompt)
        self.assertIn(record.title, result.user_prompt)
        self.assertEqual(result.raw_model_output, "raw-json")
        self.assertEqual(result.facts_count, 1)
        self.assertEqual(run.request_count, 1)
        self.assertEqual(run.success_count, 1)
        self.assertEqual(run.validation_passed_count, 1)
        self.assertEqual(run.facts_count, 1)

    def test_api_failure_is_saved_and_does_not_stop_next_news(self):
        first = make_record(title="First")
        second = make_record(title="Second", summary="Company announced Product A.")

        def factory(article):
            if article.news_id == first.id:
                return {
                    "processing_status": "ai_call_failed",
                    "api_response": {"error": "rate limit"},
                    "raw_model_output": None,
                    "parsed_result": None,
                    "json_parse_error": None,
                    "validation": {
                        "errors": [{"code": "AI_HTTP_ERROR", "message": "HTTP 429"}],
                        "warnings": [],
                        "evidence_matches": [],
                    },
                }
            return processed_success(article)

        run = run_objective_fact_extraction(client=FakeClient(factory))

        failed = ObjectiveFactExtractionResult.objects.get(news_record=first)
        self.assertEqual(failed.extraction_status, "failed")
        self.assertFalse(failed.ai_call_succeeded)
        self.assertEqual(run.failed_count, 1)
        self.assertEqual(run.success_count, 1)
        self.assertEqual(run.status, "partial")

    def test_json_parse_failure_keeps_raw_output_and_parse_error(self):
        record = make_record()
        client = FakeClient(
            lambda article: {
                "processing_status": "json_parse_failed",
                "api_response": {"model": "deepseek-test"},
                "raw_model_output": "not-json",
                "parsed_result": None,
                "json_parse_error": "Expecting value（第 1 行，第 1 列）",
                "validation": {
                    "errors": [{"code": "MODEL_OUTPUT_JSON_ERROR", "message": "JSON 错误"}],
                    "warnings": [],
                    "evidence_matches": [],
                },
            }
        )

        run_objective_fact_extraction(client=client)

        result = ObjectiveFactExtractionResult.objects.get(news_record=record)
        self.assertTrue(result.ai_call_succeeded)
        self.assertFalse(result.json_parse_succeeded)
        self.assertEqual(result.extraction_status, "failed")
        self.assertEqual(result.raw_model_output, "not-json")
        self.assertIn("Expecting value", result.json_parse_error)

    def test_parsed_result_with_validation_errors_is_still_extraction_success(self):
        record = make_record(summary="Company announced Product A.")
        parsed = valid_parsed(record.summary)
        validation = {
            "errors": [{"code": "FACT_TIME_NOT_IN_EVIDENCE", "message": "机械校验错误"}],
            "warnings": [],
            "evidence_matches": [],
        }
        run = run_objective_fact_extraction(
            client=FakeClient(
                lambda article: processed_success(
                    article, parsed=parsed, validation=validation
                )
            )
        )

        result = ObjectiveFactExtractionResult.objects.get(news_record=record)
        self.assertEqual(result.extraction_status, "success")
        self.assertEqual(result.validation_status, "error")
        self.assertEqual(result.parsed_result, parsed)
        self.assertEqual(run.success_count, 1)
        self.assertEqual(run.validation_error_count, 1)

    def test_incremental_skip_failed_retry_and_new_version_history(self):
        record = make_record(summary="Company announced Product A.")
        success_client = FakeClient(processed_success)
        first = run_objective_fact_extraction(client=success_client)
        second = run_objective_fact_extraction(client=FakeClient(processed_success))
        self.assertEqual(first.success_count, 1)
        self.assertEqual(second.request_count, 0)
        self.assertEqual(second.skipped_count, 1)

        failed_record = make_record(title="Retry me")
        failure = RuntimeError("transport")
        run_objective_fact_extraction(
            record_ids=[failed_record.id],
            client=FakeClient(lambda article: failure),
        )
        retry = run_objective_fact_extraction(
            mode=ObjectiveFactExtractionRun.Mode.RETRY_FAILED,
            record_ids=[failed_record.id],
            client=FakeClient(processed_success),
        )
        self.assertEqual(retry.success_count, 1)
        self.assertEqual(
            ObjectiveFactExtractionResult.objects.filter(
                news_record=failed_record
            ).count(),
            2,
        )

        run_objective_fact_extraction(
            record_ids=[record.id],
            prompt_version="objective-news-facts-v2",
            client=FakeClient(processed_success),
        )
        self.assertEqual(
            set(
                ObjectiveFactExtractionResult.objects.filter(news_record=record)
                .values_list("prompt_version", flat=True)
            ),
            {"objective-news-facts-v1", "objective-news-facts-v2"},
        )


@override_settings(
    NEWS_AI_API_KEY="test-key",
    NEWS_AI_MODEL="deepseek-test",
    NEWS_OBJECTIVE_FACT_PROMPT_VERSION="objective-news-facts-v1.1",
)
class ObjectiveFactFullChainTests(TestCase):
    def test_title_only_key_event_reaches_formal_facts_storage(self):
        title = "CFTC Resolves Action Against Celsius Founder"
        record = make_record(title=title, summary="")
        parsed = {
            "event_title": title,
            "event_time": None,
            "actors": ["CFTC"],
            "action": "resolves action",
            "object": ["Celsius Founder"],
            "event_status": "occurred",
            "facts": [
                {
                    "statement": title,
                    "claim_type": "confirmed_event",
                    "evidence_text": title,
                    "fact_time": None,
                    "amounts": [],
                }
            ],
            "objective_summary": title,
            "information_completeness": "partial",
        }
        client = full_chain_client([parsed])
        try:
            run_objective_fact_extraction(
                record_ids=[record.id], client=client
            )
        finally:
            client.close()

        saved = ObjectiveFactExtractionResult.objects.get(news_record=record)
        self.assertIn(title, saved.user_prompt)
        self.assertEqual(saved.input_snapshot["title"], title)
        self.assertEqual(saved.parsed_result["facts"], parsed["facts"])
        self.assertEqual(saved.facts_count, 1)
        self.assertEqual(saved.validation_status, "passed")
        self.assertEqual(saved.evidence_matches[0]["matched_field"], "title")

    def test_same_title_and_summary_fact_is_deduplicated_and_traced(self):
        title = "Company Acquires Product A"
        summary = "Company acquires Product A."
        record = make_record(title=title, summary=summary)
        fact = {
            "statement": "Company acquires Product A.",
            "claim_type": "confirmed_event",
            "fact_time": None,
            "amounts": [],
        }
        parsed = {
            "event_title": title,
            "event_time": None,
            "actors": ["Company"],
            "action": "acquires",
            "object": ["Product A"],
            "event_status": "occurred",
            "facts": [
                {**fact, "evidence_text": title},
                {**fact, "evidence_text": summary},
            ],
            "objective_summary": "Company acquires Product A.",
            "information_completeness": "sufficient",
        }
        client = full_chain_client([parsed])
        try:
            run_objective_fact_extraction(record_ids=[record.id], client=client)
        finally:
            client.close()

        saved = ObjectiveFactExtractionResult.objects.get(news_record=record)
        self.assertEqual(len(json.loads(saved.raw_model_output)["facts"]), 2)
        self.assertEqual(saved.facts_count, 1)
        self.assertEqual(len(saved.parsed_result["facts"]), 1)
        self.assertEqual(saved.validation_status, "warning")
        self.assertIn(
            "DUPLICATE_FACT_REMOVED",
            {warning["code"] for warning in saved.validation_warnings},
        )

    def test_opinion_only_title_does_not_create_fact(self):
        title = "Op-Ed | What Markets Might Do Next"
        record = make_record(title=title, summary="")
        parsed = {
            "event_title": None,
            "event_time": None,
            "actors": [],
            "action": None,
            "object": [],
            "event_status": "unknown",
            "facts": [],
            "objective_summary": None,
            "information_completeness": "insufficient",
        }
        client = full_chain_client([parsed])
        try:
            run_objective_fact_extraction(record_ids=[record.id], client=client)
        finally:
            client.close()

        saved = ObjectiveFactExtractionResult.objects.get(news_record=record)
        self.assertEqual(saved.facts_count, 0)
        self.assertEqual(saved.parsed_result["facts"], [])
        self.assertEqual(saved.validation_status, "passed")

    def test_every_declared_event_status_validates_and_is_saved(self):
        records = [
            make_record(
                title=f"Status {status}",
                summary=f"Company announced Product {index}.",
            )
            for index, status in enumerate(EVENT_STATUS_VALUES)
        ]
        parsed_results = []
        for index, status in enumerate(EVENT_STATUS_VALUES):
            evidence = f"Company announced Product {index}."
            parsed_results.append(
                {
                    "event_title": evidence,
                    "event_time": None,
                    "actors": ["Company"],
                    "action": "announced",
                    "object": [f"Product {index}"],
                    "event_status": status,
                    "facts": [
                        {
                            "statement": evidence,
                            "claim_type": "company_claim",
                            "evidence_text": evidence,
                            "fact_time": None,
                            "amounts": [],
                        }
                    ],
                    "objective_summary": evidence,
                    "information_completeness": "sufficient",
                }
            )
        client = full_chain_client(parsed_results)
        try:
            run_objective_fact_extraction(
                record_ids=[record.id for record in records], client=client
            )
        finally:
            client.close()

        saved = ObjectiveFactExtractionResult.objects.filter(
            news_record__in=records
        )
        self.assertEqual(saved.count(), len(EVENT_STATUS_VALUES))
        self.assertEqual(
            set(saved.values_list("event_status", flat=True)),
            set(EVENT_STATUS_VALUES),
        )
        self.assertFalse(saved.exclude(validation_status="passed").exists())

    def test_illegal_event_status_is_not_converted_and_has_explicit_error(self):
        record = make_record(
            title="Media report", summary="Media reported Company action."
        )
        parsed = valid_parsed("Media reported Company action.")
        parsed["event_status"] = "reported"
        client = full_chain_client([parsed])
        try:
            run_objective_fact_extraction(record_ids=[record.id], client=client)
        finally:
            client.close()

        saved = ObjectiveFactExtractionResult.objects.get(news_record=record)
        self.assertEqual(saved.event_status, "reported")
        self.assertEqual(saved.validation_status, "error")
        self.assertIn(
            "INVALID_EVENT_STATUS",
            {error["code"] for error in saved.validation_errors},
        )
