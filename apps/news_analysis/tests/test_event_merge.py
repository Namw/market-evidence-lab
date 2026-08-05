from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.news_data.models import NewsSource

from ..event_ai import EventAIError
from ..event_merge import (
    EventMergeAlreadyRunning,
    current_objective_fact_results,
    estimate_event_merge_work,
    hard_rejection_reason,
    retry_failed_event_pairs,
    run_event_merge,
)
from ..models import (
    CanonicalEvent,
    EventMembership,
    EventMergeRun,
    EventPairDecision,
    ObjectiveFactExtractionResult,
    ObjectiveFactExtractionRun,
)
from .helpers import NOW, make_record


def make_fact_result(
    *,
    title="Binance announces listing ABC",
    actors=None,
    action="announces listing",
    objects=None,
    event_status="announced",
    errors=None,
    prompt_version="objective-news-facts-v1.1",
    published_offset=0,
    event_time=None,
    source_code="binance_announcements",
):
    record = make_record(source_code=source_code, title=title, summary=title)
    record.published_at = NOW + timedelta(hours=published_offset)
    record.save(update_fields=["published_at"])
    extraction_run = ObjectiveFactExtractionRun.objects.create(
        trigger=ObjectiveFactExtractionRun.Trigger.COMMAND,
        mode=ObjectiveFactExtractionRun.Mode.INCREMENTAL,
        status=ObjectiveFactExtractionRun.Status.SUCCESS,
        provider="DeepSeek",
        model="deepseek-v4-flash",
        prompt_version=prompt_version,
        generation_parameters={},
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    parsed = {
        "event_title": title,
        "event_time": event_time,
        "actors": actors if actors is not None else ["Binance"],
        "action": action,
        "object": objects if objects is not None else ["ABC"],
        "event_status": event_status,
        "facts": [
            {
                "statement": title,
                "claim_type": "confirmed_event",
                "evidence_text": title,
                "fact_time": event_time,
                "amounts": [],
            }
        ],
        "objective_summary": title,
        "information_completeness": "sufficient",
    }
    return ObjectiveFactExtractionResult.objects.create(
        news_record=record,
        extraction_run=extraction_run,
        extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS,
        validation_status=(
            ObjectiveFactExtractionResult.ValidationStatus.ERROR
            if errors
            else ObjectiveFactExtractionResult.ValidationStatus.PASSED
        ),
        ai_call_succeeded=True,
        json_parse_succeeded=True,
        provider="DeepSeek",
        model="deepseek-v4-flash",
        prompt_version=prompt_version,
        generation_parameters={},
        system_prompt="stored",
        user_prompt="stored",
        parsed_result=parsed,
        validation_errors=errors or [],
        objective_summary=title,
        event_status=event_status,
        information_completeness="sufficient",
        facts_count=1,
        extracted_at=timezone.now(),
    )


def relation_payload(relation, confidence=0.96, *, conflict=False, title=None):
    return {
        "relation": relation,
        "confidence": confidence,
        "same_event_basis": ["same action and object"] if relation == "same_event" else [],
        "differences": [] if relation == "same_event" else ["different occurrence"],
        "reason": f"decision: {relation}",
        "canonical_title": title,
        "has_fact_conflict": conflict,
    }


class MappingClient:
    def __init__(self, mapping=None, default="same_event"):
        self.mapping = mapping or {}
        self.default = default
        self.calls = []

    def compare(self, left, right):
        key = tuple(sorted((left["result_id"], right["result_id"])))
        self.calls.append(key)
        value = self.mapping.get(key, self.default)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict):
            return value
        return relation_payload(value, title=left["event_title"] if value == "same_event" else None)


class EventMergeSelectionTests(TestCase):
    def test_ineligible_latest_result_is_excluded_and_old_eligible_is_not_used(self):
        old = make_fact_result(title="Old eligible")
        latest = make_fact_result(title="Latest invalid")
        latest.news_record = old.news_record
        latest.validation_errors = [{"code": "INVALID"}]
        latest.save(update_fields=["news_record", "validation_errors"])

        results, latest_count = current_objective_fact_results()

        self.assertEqual(latest_count, 1)
        self.assertEqual(results, [])

    def test_other_prompt_version_does_not_replace_current_version(self):
        current = make_fact_result(title="Current")
        make_fact_result(title="Future", prompt_version="future-v2")

        results, _ = current_objective_fact_results()

        self.assertIn(current, results)

    def test_estimate_is_read_only_and_reports_hard_rejections(self):
        make_fact_result(title="Bridge A attacked", action="attacked", objects=["Bridge A"])
        make_fact_result(
            title="Bridge B attacked",
            action="attacked",
            objects=["Bridge B"],
            published_offset=1,
        )

        estimate = estimate_event_merge_work()

        self.assertEqual(EventMergeRun.objects.count(), 0)
        self.assertEqual(estimate["eligible_count"], 2)
        self.assertEqual(estimate["hard_rejected_count"], 1)
        self.assertEqual(estimate["estimated_ai_calls"], 0)


class EventMergeBuildTests(TestCase):
    def test_unrelated_status_difference_is_not_a_hard_rejection(self):
        left = {
            "event_title": "Tether signs an MoU",
            "actors": ["Tether"],
            "action": "signs",
            "object": ["Nairobi Securities Exchange MoU"],
            "event_status": "occurred",
            "objective_summary": "Tether signed an MoU.",
        }
        right = {
            "event_title": "Coldcard exploit continues",
            "actors": ["attacker"],
            "action": "steals",
            "object": ["Coldcard wallets"],
            "event_status": "ongoing",
            "objective_summary": "Coldcard losses continue.",
        }

        self.assertIsNone(hard_rejection_reason(left, right))

    def test_unrelated_news_do_not_enter_candidate_ai_comparison(self):
        make_fact_result(
            title="Tether signs Nairobi exchange MoU",
            actors=["Tether"],
            action="signs MoU",
            objects=["Nairobi Securities Exchange"],
        )
        make_fact_result(
            title="Coldcard exploit losses continue",
            actors=["attacker"],
            action="steals bitcoin",
            objects=["Coldcard wallets"],
            event_status="ongoing",
            published_offset=1,
        )
        client = MappingClient(default="not_same_event")

        run = run_event_merge(client=client)

        self.assertEqual(client.calls, [])
        self.assertEqual(run.candidate_pair_count, 0)
        self.assertEqual(run.events.count(), 2)

    def test_same_incident_update_at_threshold_is_auto_grouped(self):
        left = make_fact_result(
            title="Coldcard exploit leads to $38 million in losses",
            actors=["attacker"],
            action="exploited",
            objects=["Coldcard wallets"],
            event_status="occurred",
        )
        right = make_fact_result(
            title="Coldcard wallet losses may reach $114 million",
            actors=["attacker"],
            action="continues stealing",
            objects=["Coldcard wallets"],
            event_status="ongoing",
            published_offset=1,
        )
        payload = relation_payload(
            "same_event", confidence=0.85, title=left.parsed_result["event_title"]
        )

        run = run_event_merge(client=MappingClient({(left.id, right.id): payload}))

        self.assertEqual(run.events.count(), 1)
        self.assertEqual(run.events.get().member_count, 2)

    def test_shared_incident_background_does_not_merge_different_primary_actions(self):
        make_fact_result(
            title="Coldcard exploit prompts holders to move funds to exchanges",
            actors=["bitcoin holders"],
            action="moving funds onto exchanges for safety",
            objects=["bitcoin"],
            event_status="ongoing",
        )
        make_fact_result(
            title="Bitcoin slips under $63,000 as Coldcard losses rattle market",
            actors=["Bitcoin", "Coldcard"],
            action="slips under $63,000",
            objects=[],
            event_status="occurred",
            published_offset=1,
        )
        client = MappingClient(default="same_event")

        run = run_event_merge(client=client)

        self.assertEqual(client.calls, [])
        self.assertEqual(run.pair_decisions.get().relation, EventPairDecision.Relation.HARD_REJECTED)
        self.assertEqual(run.events.count(), 2)

    def test_same_listing_descriptions_are_grouped_once(self):
        left = make_fact_result()
        right = make_fact_result(
            title="Media reports Binance will list ABC token",
            action="will list",
            objects=["ABC token"],
            event_status="planned",
            published_offset=2,
        )
        client = MappingClient()

        run = run_event_merge(client=client)

        self.assertEqual(run.status, EventMergeRun.Status.SUCCEEDED)
        event = run.events.get()
        self.assertEqual(event.grouping_method, CanonicalEvent.GroupingMethod.AUTO_GROUPED)
        self.assertEqual(event.member_count, 2)
        self.assertEqual(
            set(event.memberships.values_list("extraction_result_id", flat=True)),
            {left.id, right.id},
        )

    def test_different_attack_objects_are_hard_rejected_without_ai(self):
        make_fact_result(title="Attack on Verus bridge", action="attacked", objects=["Verus Ethereum Bridge"])
        make_fact_result(
            title="Attack on Crypto DAO Pro",
            action="attacked",
            objects=["Crypto DAO Pro contract"],
            published_offset=1,
        )
        client = MappingClient()

        run = run_event_merge(client=client)

        self.assertEqual(client.calls, [])
        self.assertEqual(run.events.count(), 2)
        self.assertEqual(run.pair_decisions.get().relation, EventPairDecision.Relation.HARD_REJECTED)

    def test_same_actor_different_object_is_not_grouped(self):
        make_fact_result(title="SEC sues exchange A", actors=["SEC"], action="sues", objects=["Exchange A"])
        make_fact_result(
            title="SEC sues exchange B",
            actors=["SEC"],
            action="sues",
            objects=["Exchange B"],
            published_offset=1,
        )

        run = run_event_merge(client=MappingClient())

        self.assertEqual(run.events.count(), 2)

    def test_new_action_or_stage_is_separate(self):
        make_fact_result(title="SEC files lawsuit", actors=["SEC"], action="sues", objects=["Exchange A"])
        make_fact_result(
            title="Exchange A responds",
            actors=["Exchange A"],
            action="responded",
            objects=["SEC lawsuit"],
            event_status="occurred",
            published_offset=1,
        )

        run = run_event_merge(client=MappingClient())

        self.assertEqual(run.events.count(), 2)
        self.assertEqual(run.pair_decisions.get().relation, EventPairDecision.Relation.HARD_REJECTED)

    def test_missing_fields_are_not_a_hard_conflict(self):
        make_fact_result(title="Listing ABC", action="lists", objects=["ABC"])
        make_fact_result(
            title="ABC listing report",
            action="lists",
            objects=[],
            published_offset=1,
        )
        client = MappingClient(default="uncertain")

        run = run_event_merge(client=client)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(run.pair_decisions.get().relation, EventPairDecision.Relation.UNCERTAIN)
        self.assertEqual(run.events.count(), 2)

    def test_invalid_or_transport_failure_never_merges(self):
        left = make_fact_result()
        right = make_fact_result(published_offset=1)
        error = EventAIError("AI_TRANSPORT_ERROR", "timeout", retryable=True, attempts=3)
        client = MappingClient({(left.id, right.id): error})

        run = run_event_merge(client=client)

        self.assertEqual(run.status, EventMergeRun.Status.SUCCEEDED_WITH_WARNINGS)
        self.assertEqual(run.events.count(), 2)
        decision = run.pair_decisions.get()
        self.assertEqual(decision.relation, EventPairDecision.Relation.PROCESSING_FAILED)
        self.assertTrue(decision.is_retryable)

    def test_invalid_json_shape_never_merges(self):
        left = make_fact_result(title="Malformed response left")
        right = make_fact_result(title="Malformed response right", published_offset=1)

        run = run_event_merge(client=MappingClient({(left.id, right.id): {"relation": "same_event"}}))

        self.assertEqual(run.events.count(), 2)
        decision = run.pair_decisions.get()
        self.assertEqual(decision.relation, EventPairDecision.Relation.PROCESSING_FAILED)
        self.assertFalse(decision.is_retryable)

    def test_complete_link_prevents_transitive_overmerge(self):
        a = make_fact_result(title="Protocol update A", objects=["Protocol"])
        b = make_fact_result(title="Protocol update B", objects=["Protocol"], published_offset=1)
        c = make_fact_result(title="Protocol update C", objects=["Protocol"], published_offset=2)
        mapping = {
            (a.id, b.id): relation_payload("same_event", title=a.parsed_result["event_title"]),
            (b.id, c.id): relation_payload("same_event", title=b.parsed_result["event_title"]),
            (a.id, c.id): relation_payload("not_same_event", 0.98),
        }

        run = run_event_merge(client=MappingClient(mapping))

        self.assertEqual(sorted(run.events.values_list("member_count", flat=True)), [1, 2])

    def test_publication_time_is_not_written_as_event_time(self):
        make_fact_result(title="Event without explicit time", event_time=None)

        run = run_event_merge(client=MappingClient())

        self.assertEqual(run.events.get().event_time_text, "")

    def test_source_authority_does_not_change_pair_judgment_input(self):
        left = make_fact_result()
        right = make_fact_result(source_code="coindesk", published_offset=1)
        right.news_record.source.authority_level = NewsSource.AuthorityLevel.HIGHEST
        right.news_record.source.save(update_fields=["authority_level"])
        client = MappingClient(default="uncertain")

        run_event_merge(client=client)

        self.assertEqual(client.calls, [(left.id, right.id)])

    def test_every_eligible_input_has_exactly_one_membership(self):
        results = [make_fact_result(title=f"Distinct event {index}", objects=[f"Object {index}"]) for index in range(3)]

        run = run_event_merge(client=MappingClient(default="not_same_event"))

        memberships = EventMembership.objects.filter(event__run=run)
        self.assertEqual(memberships.count(), 3)
        self.assertEqual(
            set(memberships.values_list("extraction_result_id", flat=True)),
            {item.id for item in results},
        )

    def test_failed_consistency_check_keeps_old_snapshot_current(self):
        make_fact_result(title="First event")
        old = run_event_merge(client=MappingClient())
        make_fact_result(title="Second event", published_offset=2)

        with patch(
            "apps.news_analysis.event_merge.validate_event_snapshot",
            side_effect=__import__(
                "apps.news_analysis.event_merge", fromlist=["EventMergeConsistencyError"]
            ).EventMergeConsistencyError("broken"),
        ):
            failed = run_event_merge(client=MappingClient(default="not_same_event"))

        old.refresh_from_db()
        self.assertEqual(failed.status, EventMergeRun.Status.FAILED)
        self.assertTrue(old.is_current_snapshot)
        self.assertFalse(failed.is_current_snapshot)

    def test_running_guard_and_request_key_are_idempotent(self):
        make_fact_result()
        first = run_event_merge(client=MappingClient(), request_key="same-click")
        duplicate = run_event_merge(client=MappingClient(), request_key="same-click")
        self.assertEqual(first.id, duplicate.id)
        EventMergeRun.objects.create(
            trigger=EventMergeRun.Trigger.MANUAL,
            status=EventMergeRun.Status.RUNNING,
            algorithm_version="v",
            prompt_version="p",
            model="m",
        )
        with self.assertRaises(EventMergeAlreadyRunning):
            run_event_merge(client=MappingClient())


class EventMergeRetryTests(TestCase):
    def test_retry_creates_new_run_and_preserves_failed_decision(self):
        left = make_fact_result()
        right = make_fact_result(published_offset=1)
        failed_client = MappingClient(
            {
                (left.id, right.id): EventAIError(
                    "AI_TRANSPORT_ERROR", "timeout", retryable=True, attempts=3
                )
            }
        )
        original = run_event_merge(client=failed_client)
        failed_decision = original.pair_decisions.get()

        retry = retry_failed_event_pairs(original, client=MappingClient())

        failed_decision.refresh_from_db()
        self.assertEqual(failed_decision.relation, EventPairDecision.Relation.PROCESSING_FAILED)
        self.assertEqual(retry.original_run, original)
        self.assertEqual(retry.events.get().member_count, 2)
        self.assertTrue(retry.is_current_snapshot)

    def test_business_decisions_are_not_retryable(self):
        make_fact_result()
        make_fact_result(published_offset=1)
        run = run_event_merge(client=MappingClient(default="uncertain"))

        with self.assertRaises(ValueError):
            retry_failed_event_pairs(run, client=MappingClient())


class EventMergePageAndCommandTests(TestCase):
    def test_overview_runs_events_and_details_are_accessible(self):
        make_fact_result()
        run = run_event_merge(client=MappingClient())
        event = run.events.get()
        for url in (
            "/analysis/news/events/",
            "/analysis/news/events/runs/",
            f"/analysis/news/events/runs/{run.id}/",
            "/analysis/news/events/results/",
            f"/analysis/news/events/results/{event.id}/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        overview = self.client.get("/analysis/news/events/")
        self.assertContains(overview, "当前正在使用")
        self.assertContains(overview, "最近一次处理")

    def test_mutating_entries_are_post_only_and_nonretryable_has_no_button(self):
        make_fact_result()
        make_fact_result(published_offset=1)
        run = run_event_merge(client=MappingClient(default="uncertain"))

        self.assertEqual(self.client.get("/analysis/news/events/rebuild/").status_code, 405)
        detail = self.client.get(f"/analysis/news/events/runs/{run.id}/")
        self.assertContains(detail, "业务判断结果不会显示重试按钮")

    def test_management_command_builds_snapshot(self):
        make_fact_result(title="Only event")

        with patch("apps.news_analysis.event_merge.DeepSeekEventMergeClient"):
            call_command("build_news_events")

        self.assertTrue(EventMergeRun.objects.filter(is_current_snapshot=True).exists())
