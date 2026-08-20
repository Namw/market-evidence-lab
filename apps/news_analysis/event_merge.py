from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from .event_ai import (
    DeepSeekEventMergeClient,
    EventAIError,
    EventAIResponse,
    validate_event_response,
)
from .event_matching import (
    candidate_similarity,
    generate_candidate_pairs,
    hard_rejection_reason,
    has_candidate_anchor,
    has_shared_event_anchor,
    result_snapshot as _snapshot,
)
from .models import (
    CanonicalEvent,
    EventMembership,
    EventMergeRun,
    EventPairDecision,
    ObjectiveFactExtractionResult,
)


class EventMergeAlreadyRunning(Exception):
    pass


class EventMergeConsistencyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EventMergeConfig:
    algorithm_version: str
    prompt_version: str
    model: str
    publication_window_days: int
    max_candidates_per_input: int
    min_recall_score: float
    auto_group_threshold: float

    def snapshot(self) -> dict:
        return {
            "algorithm_version": self.algorithm_version,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "publication_window_days": self.publication_window_days,
            "max_candidates_per_input": self.max_candidates_per_input,
            "min_recall_score": self.min_recall_score,
            "auto_group_threshold": self.auto_group_threshold,
        }


def get_event_merge_config() -> EventMergeConfig:
    return EventMergeConfig(
        algorithm_version=settings.NEWS_EVENT_MERGE_ALGORITHM_VERSION,
        prompt_version=settings.NEWS_EVENT_MERGE_PROMPT_VERSION,
        model=settings.NEWS_AI_MODEL,
        publication_window_days=settings.NEWS_EVENT_MERGE_WINDOW_DAYS,
        max_candidates_per_input=settings.NEWS_EVENT_MERGE_MAX_CANDIDATES,
        min_recall_score=settings.NEWS_EVENT_MERGE_MIN_RECALL_SCORE,
        auto_group_threshold=settings.NEWS_EVENT_MERGE_AUTO_THRESHOLD,
    )


def config_from_run(run: EventMergeRun) -> EventMergeConfig:
    values = run.configuration_snapshot
    return EventMergeConfig(
        algorithm_version=run.algorithm_version,
        prompt_version=run.prompt_version,
        model=run.model,
        publication_window_days=int(values["publication_window_days"]),
        max_candidates_per_input=int(values["max_candidates_per_input"]),
        min_recall_score=float(values["min_recall_score"]),
        auto_group_threshold=float(values["auto_group_threshold"]),
    )


def current_objective_fact_results() -> tuple[list[ObjectiveFactExtractionResult], int]:
    """Return eligible latest results for the current prompt, plus latest-result count."""
    prompt_version = settings.NEWS_OBJECTIVE_FACT_PROMPT_VERSION
    ordered = (
        ObjectiveFactExtractionResult.objects.filter(prompt_version=prompt_version)
        .select_related("news_record__source")
        .order_by("news_record_id", "-extracted_at", "-id")
    )
    latest: dict[int, ObjectiveFactExtractionResult] = {}
    for result in ordered:
        latest.setdefault(result.news_record_id, result)
    eligible = [result for result in latest.values() if result.is_evidence_chain_eligible]
    eligible.sort(key=lambda item: (item.news_record.published_at, item.id))
    return eligible, len(latest)


def estimate_event_merge_work() -> dict[str, int]:
    results, input_count = current_objective_fact_results()
    config = get_event_merge_config()
    pairs = generate_candidate_pairs(results, config)
    hard_rejected = sum(
        hard_rejection_reason(_snapshot(left), _snapshot(right)) is not None
        for left, right, _ in pairs
    )
    return {
        "input_count": input_count,
        "eligible_count": len(results),
        "candidate_pair_count": len(pairs),
        "hard_rejected_count": hard_rejected,
        "estimated_ai_calls": len(pairs) - hard_rejected,
    }


def event_merge_inputs_changed() -> bool:
    results, _ = current_objective_fact_results()
    current = EventMergeRun.objects.filter(is_current_snapshot=True).first()
    if current is None:
        return bool(results)
    frozen = current.configuration_snapshot.get("input_result_ids", [])
    return frozen != [item.id for item in results]


def _set_stage(run: EventMergeRun, stage: str, **values) -> None:
    run.current_stage = stage
    fields = ["current_stage", "updated_at"]
    for field, value in values.items():
        setattr(run, field, value)
        fields.append(field)
    run.save(update_fields=fields)


@transaction.atomic
def _create_run(
    *,
    trigger: str,
    config: EventMergeConfig,
    original_run: EventMergeRun | None,
    retry_pair_decision: EventPairDecision | None,
    request_key: str | None,
) -> tuple[EventMergeRun, bool]:
    if request_key:
        existing = EventMergeRun.objects.filter(request_key=request_key).first()
        if existing is not None:
            return existing, False
    if EventMergeRun.objects.select_for_update().filter(status=EventMergeRun.Status.RUNNING).exists():
        raise EventMergeAlreadyRunning("已有事件库运行正在执行，请等待完成。")
    try:
        run = EventMergeRun.objects.create(
            status=EventMergeRun.Status.RUNNING,
            trigger=trigger,
            original_run=original_run,
            retry_pair_decision=retry_pair_decision,
            request_key=request_key,
            started_at=timezone.now(),
            current_stage=EventMergeRun.Stage.SCANNING,
            algorithm_version=config.algorithm_version,
            prompt_version=config.prompt_version,
            model=config.model,
            configuration_snapshot=config.snapshot(),
        )
    except IntegrityError as exc:
        raise EventMergeAlreadyRunning("已有事件库运行正在执行，请等待完成。") from exc
    return run, True


def _frozen_results(source_run: EventMergeRun) -> tuple[list[ObjectiveFactExtractionResult], int]:
    ids = source_run.configuration_snapshot.get("input_result_ids", [])
    if not isinstance(ids, list) or any(not isinstance(item, int) for item in ids):
        raise EventMergeConsistencyError("原运行没有可用的冻结输入清单。")
    by_id = {
        result.id: result
        for result in ObjectiveFactExtractionResult.objects.filter(id__in=ids).select_related(
            "news_record__source"
        )
    }
    if set(by_id) != set(ids):
        raise EventMergeConsistencyError("原运行的冻结输入已不完整。")
    results = [by_id[item] for item in ids]
    results.sort(key=lambda item: (item.news_record.published_at, item.id))
    return results, int(source_run.configuration_snapshot.get("latest_result_count", len(results)))


def _create_hard_decision(
    run: EventMergeRun,
    config: EventMergeConfig,
    left: ObjectiveFactExtractionResult,
    right: ObjectiveFactExtractionResult,
    reason: str,
) -> EventPairDecision:
    return EventPairDecision.objects.create(
        run=run,
        left_result=left,
        right_result=right,
        relation=EventPairDecision.Relation.HARD_REJECTED,
        confidence=1.0,
        differences=[reason],
        reason=reason,
        algorithm_version=config.algorithm_version,
        prompt_version=config.prompt_version,
        model=config.model,
        processing_status=EventPairDecision.ProcessingStatus.SUCCEEDED,
    )


def _normalize_client_response(response: object, left: dict, right: dict) -> EventAIResponse:
    if isinstance(response, EventAIResponse):
        return response
    validated = validate_event_response(response, left, right)
    return EventAIResponse(
        result=validated,
        structured_response=validated,
        attempts=1,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        actual_model=settings.NEWS_AI_MODEL,
    )


def _create_ai_decision(
    run: EventMergeRun,
    config: EventMergeConfig,
    left: ObjectiveFactExtractionResult,
    right: ObjectiveFactExtractionResult,
    client,
) -> EventPairDecision:
    left_snapshot = _snapshot(left)
    right_snapshot = _snapshot(right)
    try:
        response = _normalize_client_response(client.compare(left_snapshot, right_snapshot), left_snapshot, right_snapshot)
    except EventAIError as exc:
        return EventPairDecision.objects.create(
            run=run,
            left_result=left,
            right_result=right,
            relation=EventPairDecision.Relation.PROCESSING_FAILED,
            differences=[],
            reason="模型比较未完成，保守保持独立。",
            algorithm_version=config.algorithm_version,
            prompt_version=config.prompt_version,
            model=config.model,
            processing_status=EventPairDecision.ProcessingStatus.FAILED,
            attempt_count=exc.attempts,
            last_error_code=exc.code,
            safe_error_summary=exc.safe_summary,
            is_retryable=exc.retryable,
        )
    value = response.result
    return EventPairDecision.objects.create(
        run=run,
        left_result=left,
        right_result=right,
        relation=value["relation"],
        confidence=value["confidence"],
        same_event_basis=value["same_event_basis"],
        differences=value["differences"],
        reason=value["reason"],
        canonical_title=value["canonical_title"] or "",
        has_fact_conflict=value["has_fact_conflict"],
        algorithm_version=config.algorithm_version,
        prompt_version=config.prompt_version,
        model=response.actual_model or config.model,
        structured_response=response.structured_response,
        processing_status=EventPairDecision.ProcessingStatus.SUCCEEDED,
        attempt_count=response.attempts,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        total_tokens=response.total_tokens,
    )


def _clone_decision(run: EventMergeRun, source: EventPairDecision) -> EventPairDecision:
    return EventPairDecision.objects.create(
        run=run,
        left_result=source.left_result,
        right_result=source.right_result,
        relation=source.relation,
        confidence=source.confidence,
        same_event_basis=source.same_event_basis,
        differences=source.differences,
        reason=source.reason,
        canonical_title=source.canonical_title,
        has_fact_conflict=source.has_fact_conflict,
        algorithm_version=source.algorithm_version,
        prompt_version=source.prompt_version,
        model=source.model,
        structured_response=source.structured_response,
        processing_status=source.processing_status,
        attempt_count=source.attempt_count,
        prompt_tokens=source.prompt_tokens,
        completion_tokens=source.completion_tokens,
        total_tokens=source.total_tokens,
        last_error_code=source.last_error_code,
        safe_error_summary=source.safe_error_summary,
        is_retryable=source.is_retryable,
    )


def _decision_key(left_id: int, right_id: int) -> tuple[int, int]:
    return tuple(sorted((left_id, right_id)))


def _complete_link_groups(
    results: list[ObjectiveFactExtractionResult],
    decisions: dict[tuple[int, int], EventPairDecision],
    threshold: float,
) -> list[list[ObjectiveFactExtractionResult]]:
    groups: list[list[ObjectiveFactExtractionResult]] = []
    for result in results:
        joined = False
        for group in groups:
            pair_values = [decisions.get(_decision_key(result.id, member.id)) for member in group]
            if all(
                decision is not None
                and decision.relation == EventPairDecision.Relation.SAME_EVENT
                and decision.confidence is not None
                and decision.confidence >= threshold
                and not decision.has_fact_conflict
                for decision in pair_values
            ):
                group.append(result)
                joined = True
                break
        if not joined:
            groups.append([result])
    return groups


def _event_status_for_group(
    group: list[ObjectiveFactExtractionResult],
    decisions: dict[tuple[int, int], EventPairDecision],
) -> str:
    member_ids = {item.id for item in group}
    related = [
        decision
        for key, decision in decisions.items()
        if member_ids.intersection(key)
    ]
    if any(decision.has_fact_conflict for decision in related):
        return CanonicalEvent.Status.CONFLICTED
    if len(group) == 1 and any(
        decision.relation
        in {EventPairDecision.Relation.UNCERTAIN, EventPairDecision.Relation.PROCESSING_FAILED}
        for decision in related
    ):
        return CanonicalEvent.Status.UNCERTAIN
    return CanonicalEvent.Status.PROVISIONAL


def _create_events(
    run: EventMergeRun,
    results: list[ObjectiveFactExtractionResult],
    decisions: dict[tuple[int, int], EventPairDecision],
    config: EventMergeConfig,
) -> None:
    groups = _complete_link_groups(results, decisions, config.auto_group_threshold)
    for group in groups:
        primary = group[0]
        primary_snapshot = _snapshot(primary)
        publication_times = [member.news_record.published_at for member in group]
        source_ids = {member.news_record.source_id for member in group}
        internal_decisions = [
            decisions[_decision_key(left.id, right.id)]
            for index, left in enumerate(group)
            for right in group[index + 1 :]
        ]
        titles = [
            decision.canonical_title
            for decision in internal_decisions
            if decision.canonical_title
        ]
        title = titles[0] if titles else primary_snapshot.get("event_title")
        if not isinstance(title, str) or not title.strip():
            title = primary.news_record.title
        confidences = [
            decision.confidence
            for decision in internal_decisions
            if decision.confidence is not None
        ]
        grouping_method = (
            CanonicalEvent.GroupingMethod.AUTO_GROUPED
            if len(group) > 1
            else CanonicalEvent.GroupingMethod.SINGLETON
        )
        event = CanonicalEvent.objects.create(
            run=run,
            canonical_title=title.strip(),
            status=_event_status_for_group(group, decisions),
            grouping_method=grouping_method,
            actors_snapshot=primary_snapshot.get("actors", []),
            action_snapshot=primary_snapshot.get("action") or "",
            object_snapshot=primary_snapshot.get("object", []),
            event_status_snapshot=primary_snapshot.get("event_status") or "",
            objective_summary=primary_snapshot.get("objective_summary") or "",
            event_time_text=primary_snapshot.get("event_time") or "",
            earliest_publication_at=min(publication_times),
            latest_publication_at=max(publication_times),
            member_count=len(group),
            source_count=len(source_ids),
            grouping_confidence=min(confidences) if confidences else None,
        )
        for index, member in enumerate(group):
            member_decisions = [
                decisions[_decision_key(member.id, other.id)]
                for other in group
                if other.id != member.id
            ]
            EventMembership.objects.create(
                event=event,
                extraction_result=member,
                news_record=member.news_record,
                member_role=(
                    EventMembership.Role.PRIMARY
                    if index == 0
                    else EventMembership.Role.CORROBORATING
                ),
                join_method=(
                    EventMembership.JoinMethod.AUTO_MATCH
                    if len(group) > 1
                    else EventMembership.JoinMethod.SINGLETON
                ),
                match_confidence=(
                    min(
                        decision.confidence
                        for decision in member_decisions
                        if decision.confidence is not None
                    )
                    if member_decisions
                    else None
                ),
                match_reason=(member_decisions[0].reason if member_decisions else "没有可合并候选，保守建立单成员事件。"),
            )


def validate_event_snapshot(run: EventMergeRun, expected_result_ids: Iterable[int]) -> None:
    expected = list(expected_result_ids)
    memberships = EventMembership.objects.filter(event__run=run)
    actual = list(memberships.values_list("extraction_result_id", flat=True))
    if len(actual) != len(expected) or sorted(actual) != sorted(expected):
        raise EventMergeConsistencyError("并非每条合格输入都恰好归属一次。")
    if memberships.exclude(event__run=run).exists():
        raise EventMergeConsistencyError("存在跨运行成员。")
    if memberships.exclude(news_record_id__isnull=False).exists():
        raise EventMergeConsistencyError("存在缺少新闻引用的成员。")
    duplicate_results = (
        memberships.values("extraction_result_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    if duplicate_results.exists():
        raise EventMergeConsistencyError("存在重复成员。")
    event_total = sum(run.events.values_list("member_count", flat=True))
    if event_total != len(actual):
        raise EventMergeConsistencyError("事件成员汇总不一致。")


@transaction.atomic
def _activate(run: EventMergeRun) -> None:
    EventMergeRun.objects.select_for_update().filter(is_current_snapshot=True).exclude(pk=run.pk).update(
        is_current_snapshot=False
    )
    run.is_current_snapshot = True
    run.save(update_fields=["is_current_snapshot", "updated_at"])


def _sync_counts(run: EventMergeRun) -> None:
    counts = {
        relation: total
        for relation, total in run.pair_decisions.values_list("relation").annotate(total=Count("id"))
    }
    run.hard_rejected_count = counts.get(EventPairDecision.Relation.HARD_REJECTED, 0)
    run.ai_failure_count = counts.get(EventPairDecision.Relation.PROCESSING_FAILED, 0)
    run.ai_decision_count = run.candidate_pair_count - run.hard_rejected_count
    run.prompt_tokens = sum(run.pair_decisions.values_list("prompt_tokens", flat=True))
    run.completion_tokens = sum(run.pair_decisions.values_list("completion_tokens", flat=True))
    run.total_tokens = sum(run.pair_decisions.values_list("total_tokens", flat=True))
    run.auto_grouped_event_count = run.events.filter(
        grouping_method=CanonicalEvent.GroupingMethod.AUTO_GROUPED
    ).count()
    run.singleton_event_count = run.events.filter(
        grouping_method=CanonicalEvent.GroupingMethod.SINGLETON
    ).count()
    run.uncertain_event_count = run.events.filter(
        status=CanonicalEvent.Status.UNCERTAIN
    ).count()
    run.final_event_count = run.events.count()
    run.save(
        update_fields=[
            "hard_rejected_count",
            "ai_failure_count",
            "ai_decision_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "auto_grouped_event_count",
            "singleton_event_count",
            "uncertain_event_count",
            "final_event_count",
            "updated_at",
        ]
    )


def run_event_merge(
    *,
    trigger: str = EventMergeRun.Trigger.FULL_REBUILD,
    original_run: EventMergeRun | None = None,
    retry_pair_decision: EventPairDecision | None = None,
    request_key: str | None = None,
    client=None,
) -> EventMergeRun:
    if trigger == EventMergeRun.Trigger.RETRY_FAILED and original_run is None:
        raise ValueError("重试失败项必须指定原运行。")
    if retry_pair_decision is not None:
        if retry_pair_decision.run_id != original_run.id or not retry_pair_decision.is_retryable:
            raise ValueError("该判断不属于指定运行的可重试失败项。")
    config = config_from_run(original_run) if original_run else get_event_merge_config()
    run, created = _create_run(
        trigger=trigger,
        config=config,
        original_run=original_run,
        retry_pair_decision=retry_pair_decision,
        request_key=request_key,
    )
    if not created:
        return run
    owns_client = client is None
    ai_client = client or DeepSeekEventMergeClient()
    try:
        if original_run:
            results, input_count = _frozen_results(original_run)
        else:
            results, input_count = current_objective_fact_results()
        run.input_count = input_count
        run.eligible_count = len(results)
        run.total_progress = len(results)
        snapshot = config.snapshot()
        snapshot["input_result_ids"] = [item.id for item in results]
        snapshot["latest_result_count"] = input_count
        run.configuration_snapshot = snapshot
        run.save(
            update_fields=[
                "input_count",
                "eligible_count",
                "total_progress",
                "configuration_snapshot",
                "updated_at",
            ]
        )

        _set_stage(run, EventMergeRun.Stage.CANDIDATES)
        pairs = generate_candidate_pairs(results, config)
        run.candidate_pair_count = len(pairs)
        run.total_progress = len(results) + len(pairs)
        run.save(update_fields=["candidate_pair_count", "total_progress", "updated_at"])

        original_decisions = {}
        if original_run:
            original_decisions = {
                _decision_key(item.left_result_id, item.right_result_id): item
                for item in original_run.pair_decisions.select_related(
                    "left_result", "right_result"
                )
            }
        _set_stage(run, EventMergeRun.Stage.HARD_RULES)
        for index, (left, right, _) in enumerate(pairs, start=1):
            key = _decision_key(left.id, right.id)
            source = original_decisions.get(key)
            should_retry = (
                source is not None
                and source.relation == EventPairDecision.Relation.PROCESSING_FAILED
                and source.is_retryable
                and (retry_pair_decision is None or source.id == retry_pair_decision.id)
            )
            if source is not None and not should_retry:
                _clone_decision(run, source)
            else:
                reason = hard_rejection_reason(_snapshot(left), _snapshot(right))
                if reason:
                    _create_hard_decision(run, config, left, right, reason)
                else:
                    if run.current_stage != EventMergeRun.Stage.AI_DECISIONS:
                        _set_stage(run, EventMergeRun.Stage.AI_DECISIONS)
                    _create_ai_decision(run, config, left, right, ai_client)
            run.processed_count = len(results) + index
            run.save(update_fields=["processed_count", "updated_at"])

        decisions = {
            _decision_key(item.left_result_id, item.right_result_id): item
            for item in run.pair_decisions.all()
        }
        _set_stage(run, EventMergeRun.Stage.GROUPING)
        with transaction.atomic():
            _create_events(run, results, decisions, config)
            _set_stage(run, EventMergeRun.Stage.VALIDATING)
            validate_event_snapshot(run, [item.id for item in results])
            _sync_counts(run)
            _set_stage(run, EventMergeRun.Stage.ACTIVATING)
            run.status = (
                EventMergeRun.Status.SUCCEEDED_WITH_WARNINGS
                if run.ai_failure_count
                else EventMergeRun.Status.SUCCEEDED
            )
            run.current_stage = EventMergeRun.Stage.COMPLETED
            run.processed_count = run.total_progress
            run.finished_at = timezone.now()
            run.safe_error_summary = (
                f"{run.ai_failure_count} 个 AI 比较失败；相关输入已保守保持独立。"
                if run.ai_failure_count
                else ""
            )
            run.save(
                update_fields=[
                    "status",
                    "current_stage",
                    "processed_count",
                    "finished_at",
                    "safe_error_summary",
                    "updated_at",
                ]
            )
            _activate(run)
    except Exception as exc:
        run.status = EventMergeRun.Status.FAILED
        run.finished_at = timezone.now()
        run.safe_error_summary = f"事件库构建失败：{type(exc).__name__}。"[:500]
        run.save(update_fields=["status", "finished_at", "safe_error_summary", "updated_at"])
        if isinstance(exc, (EventMergeConsistencyError, ValueError)):
            return run
        raise
    finally:
        if owns_client:
            ai_client.close()
    return run


def retry_failed_event_pairs(
    original_run: EventMergeRun,
    *,
    pair_decision: EventPairDecision | None = None,
    request_key: str | None = None,
    client=None,
) -> EventMergeRun:
    retryable = original_run.pair_decisions.filter(
        relation=EventPairDecision.Relation.PROCESSING_FAILED, is_retryable=True
    )
    if pair_decision is not None and not retryable.filter(pk=pair_decision.pk).exists():
        raise ValueError("该失败项不可重试。")
    if pair_decision is None and not retryable.exists():
        raise ValueError("该运行没有可重试失败项。")
    return run_event_merge(
        trigger=EventMergeRun.Trigger.RETRY_FAILED,
        original_run=original_run,
        retry_pair_decision=pair_decision,
        request_key=request_key,
        client=client,
    )
