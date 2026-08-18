from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.news_analysis.models import (
    ObjectiveFactExtractionResult,
    ObjectiveFactExtractionRun,
)
from apps.news_analysis.objective_facts import (
    ObjectiveFactAlreadyRunning,
    objective_fact_selection_count,
    run_objective_fact_extraction,
)

from .helpers import make_record


PROMPT = "objective-news-facts-v1.1"
OLD_PROMPT = "objective-news-facts-v1"


def make_run(**overrides):
    values = {
        "trigger": ObjectiveFactExtractionRun.Trigger.COMMAND,
        "triggered_by": "test",
        "mode": ObjectiveFactExtractionRun.Mode.INCREMENTAL,
        "status": ObjectiveFactExtractionRun.Status.SUCCESS,
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
    run = overrides.pop("extraction_run", None) or make_run(
        prompt_version=overrides.get("prompt_version", PROMPT)
    )
    values = {
        "news_record": record,
        "extraction_run": run,
        "extraction_status": ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS,
        "validation_status": ObjectiveFactExtractionResult.ValidationStatus.PASSED,
        "ai_call_succeeded": True,
        "json_parse_succeeded": True,
        "provider": "DeepSeek",
        "model": "deepseek-test",
        "prompt_version": PROMPT,
        "generation_parameters": {"temperature": 0},
        "input_snapshot": {"title": record.title, "summary": record.summary},
        "system_prompt": "system",
        "user_prompt": "user",
        "parsed_result": {
            "facts": [{"statement": "Fact", "evidence_text": record.title}],
            "objective_summary": "Fact",
        },
        "objective_summary": "Fact",
        "facts_count": 1,
        "validation_errors": [],
        "validation_warnings": [],
        "extracted_at": timezone.now(),
    }
    values.update(overrides)
    return ObjectiveFactExtractionResult.objects.create(**values)


def processed(article, *, prompt_tokens=10, completion_tokens=5, warnings=None):
    return {
        "processing_status": "success",
        "api_response": {
            "model": "deepseek-returned",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
        "raw_model_output": "{}",
        "parsed_result": {
            "event_status": "announced",
            "information_completeness": "sufficient",
            "facts": [
                {
                    "statement": "Fact",
                    "claim_type": "company_claim",
                    "evidence_text": article.title,
                    "fact_time": None,
                    "amounts": [],
                }
            ],
            "objective_summary": "Fact",
        },
        "json_parse_error": None,
        "validation": {
            "errors": [],
            "warnings": warnings or [],
            "evidence_matches": [],
        },
    }


class ScriptedClient:
    def __init__(self, factory=processed):
        self.factory = factory
        self.calls = []

    def process(self, article):
        self.calls.append(article.news_id)
        value = self.factory(article)
        if isinstance(value, Exception):
            raise value
        return value


@override_settings(NEWS_OBJECTIVE_FACT_PROMPT_VERSION=PROMPT)
class ObjectiveFactExecutionServiceTests(TestCase):
    def test_batch_limit_defers_remaining_candidates_without_marking_them_processed(self):
        records = [make_record(title=f"Deferred {index}") for index in range(3)]
        client = ScriptedClient()

        run = run_objective_fact_extraction(client=client, max_records=2)

        self.assertEqual(client.calls, [records[0].id, records[1].id])
        self.assertEqual(run.candidate_count, 2)
        self.assertEqual(run.skipped_count, 1)
        self.assertFalse(
            ObjectiveFactExtractionResult.objects.filter(
                news_record=records[2],
                prompt_version=PROMPT,
            ).exists()
        )

    def test_incremental_is_version_scoped_and_skips_current_success(self):
        unextracted = make_record(title="Unextracted")
        old_only = make_record(title="Old version only")
        current = make_record(title="Current success")
        make_result(old_only, prompt_version=OLD_PROMPT)
        make_result(current, prompt_version=PROMPT)
        client = ScriptedClient()

        run = run_objective_fact_extraction(client=client)

        self.assertEqual(client.calls, [unextracted.id, old_only.id])
        self.assertEqual(run.candidate_count, 2)
        self.assertEqual(run.skipped_count, 1)
        self.assertEqual(
            ObjectiveFactExtractionResult.objects.filter(
                news_record=current, prompt_version=PROMPT
            ).count(),
            1,
        )

    def test_retry_failed_uses_latest_invalid_result_only(self):
        execution_failed = make_record(title="Execution failed")
        validation_error = make_record(title="Validation error")
        invalid_execution = make_record(title="Invalid execution")
        eligible_warning = make_record(title="Eligible warning")
        completed = make_record(title="Completed")
        make_result(
            execution_failed,
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
            ai_call_succeeded=False,
            json_parse_succeeded=False,
        )
        make_result(
            validation_error,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
            validation_errors=[{"code": "EVIDENCE_NOT_MATCHED"}],
        )
        make_result(invalid_execution, ai_call_succeeded=False)
        warning = make_result(
            eligible_warning,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.WARNING,
            validation_warnings=[{"code": "LIMITED_SOURCE_CONTEXT"}],
        )
        make_result(completed)
        self.assertTrue(warning.is_evidence_chain_eligible)
        client = ScriptedClient()

        run = run_objective_fact_extraction(
            mode=ObjectiveFactExtractionRun.Mode.RETRY_FAILED,
            client=client,
        )

        self.assertEqual(
            client.calls,
            [execution_failed.id, validation_error.id, invalid_execution.id],
        )
        self.assertEqual(run.candidate_count, 3)
        self.assertEqual(run.skipped_count, 2)

    def test_retry_ignores_old_error_when_latest_result_is_valid(self):
        record = make_record(title="Recovered")
        old = make_result(
            record,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
            extracted_at=timezone.now() - timedelta(hours=1),
        )
        latest = make_result(record)

        count, skipped = objective_fact_selection_count(
            mode=ObjectiveFactExtractionRun.Mode.RETRY_FAILED
        )

        self.assertEqual((count, skipped), (0, 1))
        self.assertGreater(latest.id, old.id)

    def test_reextract_creates_new_result_and_preserves_history(self):
        record = make_record(title="Reextract")
        original = make_result(record)

        run = run_objective_fact_extraction(
            mode=ObjectiveFactExtractionRun.Mode.REEXTRACT,
            record_ids=[record.id],
            client=ScriptedClient(),
        )

        results = list(
            ObjectiveFactExtractionResult.objects.filter(news_record=record).order_by("id")
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].id, original.id)
        self.assertEqual(results[1].extraction_run_id, run.id)

    def test_global_running_slot_blocks_batch_and_single(self):
        record = make_record()
        make_run(
            status=ObjectiveFactExtractionRun.Status.RUNNING,
            finished_at=None,
            prompt_version=OLD_PROMPT,
        )

        with self.assertRaises(ObjectiveFactAlreadyRunning):
            run_objective_fact_extraction(client=ScriptedClient())
        with self.assertRaises(ObjectiveFactAlreadyRunning):
            run_objective_fact_extraction(
                mode=ObjectiveFactExtractionRun.Mode.SINGLE,
                record_ids=[record.id],
                client=ScriptedClient(),
            )

    def test_stale_running_task_is_closed_before_next_run(self):
        interrupted = make_record(title="Interrupted")
        active = make_run(
            status=ObjectiveFactExtractionRun.Status.RUNNING,
            finished_at=None,
        )
        pending = make_result(
            interrupted,
            extraction_run=active,
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.PENDING,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
            extracted_at=None,
        )
        ObjectiveFactExtractionRun.objects.filter(pk=active.pk).update(
            updated_at=timezone.now() - timedelta(minutes=16)
        )

        replacement = run_objective_fact_extraction(client=ScriptedClient())

        active.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(active.status, ObjectiveFactExtractionRun.Status.FAILED)
        self.assertEqual(
            pending.extraction_status,
            ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
        )
        self.assertEqual(replacement.status, ObjectiveFactExtractionRun.Status.SUCCESS)

    def test_single_failure_does_not_stop_batch_and_totals_match_details(self):
        first = make_record(title="First")
        second = make_record(title="Second")

        def factory(article):
            if article.news_id == first.id:
                return RuntimeError("secret upstream detail")
            return processed(article, prompt_tokens=20, completion_tokens=7)

        run = run_objective_fact_extraction(client=ScriptedClient(factory))
        results = list(run.results.order_by("news_record_id"))

        self.assertEqual(run.processed_count, len(results), 2)
        self.assertEqual(run.request_count, sum(item.request_count for item in results), 2)
        self.assertEqual(run.success_count, 1)
        self.assertEqual(run.failed_count, 1)
        self.assertEqual(run.validation_error_count, 1)
        self.assertEqual(run.prompt_tokens, sum(item.prompt_tokens for item in results), 20)
        self.assertEqual(run.completion_tokens, 7)
        self.assertEqual(run.total_tokens, 27)
        self.assertEqual(run.status, ObjectiveFactExtractionRun.Status.PARTIAL)
        self.assertNotIn("secret upstream detail", results[0].safe_error_summary)

    def test_token_usage_is_summed_from_each_result(self):
        first = make_record(title="First tokens")
        second = make_record(title="Second tokens")

        def factory(article):
            if article.news_id == first.id:
                return processed(article, prompt_tokens=11, completion_tokens=3)
            return processed(article, prompt_tokens=19, completion_tokens=7)

        run = run_objective_fact_extraction(client=ScriptedClient(factory))

        self.assertEqual((run.prompt_tokens, run.completion_tokens, run.total_tokens), (30, 10, 40))
        self.assertEqual(run.results.count(), 2)


@override_settings(
    NEWS_OBJECTIVE_FACT_PROMPT_VERSION=PROMPT,
    NEWS_AI_API_KEY="test-key",
)
class ObjectiveFactExecutionViewTests(TestCase):
    def test_page_buttons_and_current_version_status_match_database(self):
        unextracted = make_record(title="Unextracted button")
        failed = make_record(title="Retry button")
        completed = make_record(title="Reextract button")
        old_only = make_record(title="Old version is not current")
        make_result(
            failed,
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
        )
        make_result(completed)
        make_result(old_only, prompt_version=OLD_PROMPT)

        response = self.client.get(reverse("news_analysis:objective_fact_list"))
        rows = {item.id: item for item in response.context["page"].object_list}

        self.assertEqual(rows[unextracted.id].objective_fact_action_label, "提取")
        self.assertEqual(rows[failed.id].objective_fact_action_label, "重试")
        self.assertEqual(rows[completed.id].objective_fact_action_label, "重新提取")
        self.assertIsNone(rows[old_only.id].objective_fact_result)
        self.assertContains(response, "历史版本成功结果不会显示为当前版本已提取")

    @patch("apps.news_analysis.views.run_objective_fact_extraction")
    def test_batch_preview_only_estimates_then_confirm_post_runs(self, run_service):
        make_record(title="Candidate")
        preview = self.client.get(
            reverse(
                "news_analysis:objective_fact_run_confirm",
                args=[ObjectiveFactExtractionRun.Mode.INCREMENTAL],
            )
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context["candidate_count"], 1)
        self.assertContains(preview, "此页面只做候选预估，不会调用模型")
        run_service.assert_not_called()
        run = make_run(mode=ObjectiveFactExtractionRun.Mode.INCREMENTAL)
        run_service.return_value = run

        response = self.client.post(
            reverse(
                "news_analysis:objective_fact_run",
                args=[ObjectiveFactExtractionRun.Mode.INCREMENTAL],
            ),
            {"confirm": "yes"},
        )

        self.assertRedirects(
            response,
            reverse("news_analysis:objective_fact_run_detail", args=[run.id]),
            fetch_redirect_response=False,
        )
        run_service.assert_called_once()

    @patch("apps.news_analysis.views.run_objective_fact_extraction")
    def test_single_post_records_authenticated_operator(self, run_service):
        record = make_record()
        user = get_user_model().objects.create_user(username="operator", password="x")
        self.client.force_login(user)
        run = make_run(mode=ObjectiveFactExtractionRun.Mode.SINGLE)
        run_service.return_value = run

        response = self.client.post(
            reverse(
                "news_analysis:objective_fact_single_run",
                args=[record.id, ObjectiveFactExtractionRun.Mode.SINGLE],
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(run_service.call_args.kwargs["triggered_by"], "operator")
        self.assertEqual(run_service.call_args.kwargs["record_ids"], [record.id])

    def test_post_csrf_http_methods_and_illegal_parameters(self):
        record = make_record()
        csrf_client = Client(enforce_csrf_checks=True)
        batch_url = reverse(
            "news_analysis:objective_fact_run",
            args=[ObjectiveFactExtractionRun.Mode.INCREMENTAL],
        )
        single_url = reverse(
            "news_analysis:objective_fact_single_run",
            args=[record.id, ObjectiveFactExtractionRun.Mode.SINGLE],
        )

        self.assertEqual(csrf_client.post(batch_url, {"confirm": "yes"}).status_code, 403)
        self.assertEqual(self.client.get(batch_url).status_code, 405)
        self.assertEqual(self.client.get(single_url).status_code, 405)
        invalid = self.client.post(
            reverse("news_analysis:objective_fact_run", args=["invalid"]),
            {"confirm": "yes"},
        )
        self.assertRedirects(
            invalid,
            reverse("news_analysis:objective_fact_list"),
            fetch_redirect_response=False,
        )

    @patch("apps.news_analysis.views.run_objective_fact_extraction")
    def test_illegal_single_action_does_not_start_service(self, run_service):
        record = make_record()
        make_result(record)

        response = self.client.post(
            reverse(
                "news_analysis:objective_fact_single_run",
                args=[record.id, ObjectiveFactExtractionRun.Mode.SINGLE],
            )
        )

        self.assertRedirects(
            response,
            reverse("news_analysis:objective_fact_detail", args=[record.id]),
            fetch_redirect_response=False,
        )
        run_service.assert_not_called()

    def test_detail_defaults_to_latest_current_and_can_open_history(self):
        record = make_record(title="History")
        old_version = make_result(
            record,
            prompt_version=OLD_PROMPT,
            objective_summary="Old version",
            extracted_at=timezone.now() - timedelta(hours=2),
        )
        older_current = make_result(
            record,
            objective_summary="Older current",
            extracted_at=timezone.now() - timedelta(hours=1),
        )
        latest_current = make_result(record, objective_summary="Latest current")

        response = self.client.get(
            reverse("news_analysis:objective_fact_detail", args=[record.id])
        )

        self.assertEqual(response.context["result"].id, latest_current.id)
        self.assertEqual(
            [item.id for item in response.context["history"]],
            [latest_current.id, older_current.id, old_version.id],
        )
        historical = self.client.get(
            reverse("news_analysis:objective_fact_detail", args=[record.id]),
            {"result": old_version.id},
        )
        self.assertEqual(historical.context["result"].id, old_version.id)
        self.assertContains(historical, "当前查看的是历史版本")

    def test_run_detail_shows_summary_result_and_tokens(self):
        record = make_record(title="Audited news")
        run = make_run(
            candidate_count=1,
            processed_count=1,
            request_count=1,
            success_count=1,
            validation_passed_count=1,
            prompt_tokens=12,
            completion_tokens=4,
            total_tokens=16,
        )
        result = make_result(
            record,
            extraction_run=run,
            request_count=1,
            prompt_tokens=12,
            completion_tokens=4,
            total_tokens=16,
        )

        response = self.client.get(
            reverse("news_analysis:objective_fact_run_detail", args=[run.id])
        )

        self.assertContains(response, "Audited news")
        self.assertContains(response, "prompt 12 / completion 4 / total 16")
        self.assertContains(
            response,
            reverse("news_analysis:objective_fact_detail", args=[record.id])
            + f"?result={result.id}",
        )
