from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.news_analysis.models import (
    ObjectiveFactExtractionResult,
    ObjectiveFactExtractionRun,
)
from apps.news_analysis.objective_fact_presentation import (
    highlighted_segments,
    locate_evidence,
)

from .helpers import make_record


PROMPT = "objective-news-facts-v1"


def make_run(**overrides):
    values = {
        "trigger": "command",
        "mode": "incremental",
        "status": "success",
        "provider": "DeepSeek",
        "model": "deepseek-test",
        "prompt_version": PROMPT,
        "generation_parameters": {"temperature": 0},
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
    }
    values.update(overrides)
    return ObjectiveFactExtractionRun.objects.create(**values)


def make_result(record, **overrides):
    run = overrides.pop("extraction_run", None) or make_run()
    parsed = overrides.pop(
        "parsed_result",
        {
            "event_title": "Company event",
            "event_time": None,
            "actors": ["Company"],
            "action": "announced",
            "object": ["Product"],
            "event_status": "announced",
            "facts": [],
            "objective_summary": "Company announced Product.",
            "information_completeness": "sufficient",
        },
    )
    values = {
        "news_record": record,
        "extraction_run": run,
        "extraction_status": "success",
        "validation_status": "passed",
        "ai_call_succeeded": True,
        "json_parse_succeeded": True,
        "provider": "DeepSeek",
        "model": "deepseek-test",
        "prompt_version": PROMPT,
        "generation_parameters": {"temperature": 0},
        "input_snapshot": {
            "title": record.title,
            "summary": record.summary,
            "stored_body": "",
            "published_at": record.published_at.isoformat(),
        },
        "input_scope_note": "本次提取仅使用数据库保存的标题和摘要，未读取新闻全文。",
        "system_prompt": "<script>system</script>",
        "user_prompt": "user",
        "raw_model_output": "raw",
        "parsed_result": parsed,
        "objective_summary": parsed.get("objective_summary") or "",
        "event_status": parsed.get("event_status") or "",
        "information_completeness": parsed.get("information_completeness") or "",
        "facts_count": len(parsed.get("facts") or []),
        "extracted_at": timezone.now(),
    }
    values.update(overrides)
    return ObjectiveFactExtractionResult.objects.create(**values)


@override_settings(NEWS_OBJECTIVE_FACT_PROMPT_VERSION=PROMPT)
class ObjectiveFactViewTests(TestCase):
    def test_one_news_row_uses_latest_result(self):
        record = make_record(title="Original headline")
        old = make_result(record, objective_summary="Older summary")
        latest = make_result(record, objective_summary="Latest summary")

        response = self.client.get(reverse("news_analysis:objective_fact_list"))

        rows = list(response.context["page"].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].objective_fact_result.id, latest.id)
        self.assertContains(response, "Latest summary")
        self.assertNotContains(response, "Older summary")
        old.refresh_from_db()

    def test_combined_filters_and_pagination_query_are_preserved(self):
        matching = make_record(title="Target keyword")
        make_result(
            matching,
            event_status="announced",
            information_completeness="partial",
            validation_status="warning",
            facts_count=2,
        )
        other = make_record(title="Other")
        make_result(other, event_status="occurred", facts_count=1)
        params = {
            "source": matching.source_id,
            "keyword": "Target",
            "event_status": "announced",
            "information_completeness": "partial",
            "extraction_status": "success",
            "validation_status": "warning",
            "has_body": "no",
            "facts_count": "multiple",
            "published_start": (matching.published_at - timedelta(days=1)).date(),
            "published_end": (matching.published_at + timedelta(days=1)).date(),
        }

        response = self.client.get(reverse("news_analysis:objective_fact_list"), params)

        self.assertEqual(list(response.context["page"].object_list), [matching])
        self.assertIn("keyword=Target", response.context["pagination_query"])
        self.assertIn("validation_status=warning", response.context["pagination_query"])

    def test_unextracted_failed_validation_error_and_passed_are_distinct(self):
        unextracted = make_record(title="Unextracted")
        failed = make_record(title="Failed")
        make_result(failed, extraction_status="failed", ai_call_succeeded=False)
        invalid = make_record(title="Validation error")
        make_result(invalid, validation_status="error")
        passed = make_record(title="Passed")
        make_result(passed, validation_status="passed")

        response = self.client.get(reverse("news_analysis:objective_fact_list"))

        self.assertContains(response, "尚未提取")
        self.assertContains(response, "提取失败")
        self.assertContains(response, "校验错误")
        self.assertContains(response, "校验通过")
        self.assertContains(response, reverse("news_analysis:objective_fact_detail", args=[unextracted.id]))

    def test_detail_shows_no_body_notice_safe_link_and_escaped_content(self):
        record = make_record(
            title='<script>alert("title")</script>',
            summary='<img src=x onerror="alert(1)">',
        )
        make_result(record)

        response = self.client.get(
            reverse("news_analysis:objective_fact_detail", args=[record.id])
        )

        self.assertContains(
            response,
            "本次提取仅使用数据库保存的标题和摘要，未读取新闻全文。",
        )
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertNotContains(response, '<script>alert("title")</script>')
        self.assertContains(response, "&lt;script&gt;", html=False)
        self.assertNotContains(response, '<img src=x onerror="alert(1)">')

    def test_detail_displays_fields_and_all_evidence_match_states(self):
        record = make_record(
            title="Company announced Product.",
            summary="First   evidence. Second evidence.",
        )
        parsed = {
            "event_title": "Company event",
            "event_time": None,
            "actors": ["Company"],
            "action": "announced",
            "object": ["Product"],
            "event_status": "announced",
            "facts": [
                {"statement": "A", "claim_type": "company_claim", "evidence_text": "Company announced Product.", "fact_time": None, "amounts": []},
                {"statement": "B", "claim_type": "reported_claim", "evidence_text": "First evidence.", "fact_time": None, "amounts": []},
                {"statement": "C", "claim_type": "reported_claim", "evidence_text": "Missing evidence.", "fact_time": None, "amounts": []},
            ],
            "objective_summary": "Company announced Product.",
            "information_completeness": "partial",
        }
        make_result(
            record,
            parsed_result=parsed,
            evidence_matches=[
                {"fact_index": 0, "matched": True, "matched_field": "title", "match_type": "exact"},
                {"fact_index": 1, "matched": True, "matched_field": "summary", "match_type": "whitespace_normalized"},
                {"fact_index": 2, "matched": False, "matched_field": None, "match_type": "unmatched"},
            ],
        )

        response = self.client.get(reverse("news_analysis:objective_fact_detail", args=[record.id]))

        self.assertContains(response, "Company event")
        self.assertContains(response, "company_claim")
        self.assertContains(response, "精确匹配")
        self.assertContains(response, "空白标准化匹配")
        self.assertContains(response, "未匹配")
        self.assertContains(response, "<mark>Company announced Product.</mark>", html=True)

    def test_detail_does_not_render_missing_or_unsafe_source_link(self):
        record = make_record(title="No safe source")
        record.original_url = "javascript:alert(1)"
        record.canonical_url = ""
        record.save(update_fields=["original_url", "canonical_url"])
        make_result(record)

        response = self.client.get(
            reverse("news_analysis:objective_fact_detail", args=[record.id])
        )

        self.assertNotContains(response, "查看原文")
        self.assertNotContains(response, "javascript:alert(1)")


class EvidencePresentationTests(TestCase):
    def test_exact_whitespace_normalized_and_unmatched(self):
        self.assertEqual(locate_evidence("Exact text", "Exact text")["match_type"], "exact")
        normalized = locate_evidence("First   evidence", "First evidence")
        self.assertEqual(normalized["match_type"], "whitespace_normalized")
        self.assertEqual(locate_evidence("Text", "Missing")["match_type"], "unmatched")
        segments = highlighted_segments("First   evidence", ["First evidence"])
        self.assertTrue(any(segment["highlight"] for segment in segments))
