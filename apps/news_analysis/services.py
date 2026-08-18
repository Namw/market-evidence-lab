from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.news_data.models import NewsRawRecord, NewsSource
from apps.news_data.sources import SUMMARY_ONLY_SOURCE_CODES

from .ai import AIItem, BatchAnalysisError, DeepSeekNewsClient, TokenUsage
from .content import ArticleContent, fetch_source_article, summarize_article_text
from .models import (
    EventMembership,
    NewsAnalysisResult,
    NewsAnalysisRun,
    ObjectiveFactExtractionResult,
)
from .rules import RuleDecision, match_fixed_rule


UNCLEAR_RETENTION = timedelta(days=3)
PROTECTED_UNCLEAR_AUTHORITY_LEVELS = {
    NewsSource.AuthorityLevel.HIGHEST,
    NewsSource.AuthorityLevel.MEDIUM,
}


class AnalysisAlreadyRunning(Exception):
    pass


class AnalysisExecutionFailed(Exception):
    def __init__(self, run: NewsAnalysisRun):
        super().__init__("新闻分类运行发生内部错误。")
        self.run = run


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    batch_size: int
    max_retries: int
    max_requests_per_run: int
    analysis_version: str
    prompt_version: str


def get_analysis_config() -> AnalysisConfig:
    return AnalysisConfig(
        base_url=settings.NEWS_AI_BASE_URL,
        api_key=settings.NEWS_AI_API_KEY,
        model=settings.NEWS_AI_MODEL,
        timeout_seconds=settings.NEWS_AI_TIMEOUT_SECONDS,
        batch_size=max(1, settings.NEWS_AI_BATCH_SIZE),
        max_retries=max(0, settings.NEWS_AI_MAX_RETRIES),
        max_requests_per_run=max(1, settings.NEWS_AI_MAX_REQUESTS_PER_RUN),
        analysis_version=settings.NEWS_AI_ANALYSIS_VERSION,
        prompt_version=settings.NEWS_AI_PROMPT_VERSION,
    )


def prune_expired_news(*, now=None) -> int:
    """Delete irrelevant records and expired unclear records from general sources."""
    now = now or timezone.now()
    expired_general_unclear = Q(
        conclusion=NewsAnalysisResult.Conclusion.UNCLEAR,
        analyzed_at__lt=now - UNCLEAR_RETENTION,
    ) & ~Q(
        news_record__source__authority_level__in=PROTECTED_UNCLEAR_AUTHORITY_LEVELS
    )
    candidate_ids = list(
        NewsAnalysisResult.objects.filter(status=NewsAnalysisResult.Status.SUCCESS)
        .filter(
            Q(conclusion=NewsAnalysisResult.Conclusion.IRRELEVANT)
            | expired_general_unclear
        )
        .values_list("news_record_id", flat=True)
        .distinct()
    )
    if not candidate_ids:
        return 0
    with transaction.atomic():
        objective_facts = ObjectiveFactExtractionResult.objects.filter(
            news_record_id=OuterRef("pk")
        )
        event_memberships = EventMembership.objects.filter(
            news_record_id=OuterRef("pk")
        )
        removable_ids = list(
            NewsRawRecord.objects.select_for_update()
            .filter(id__in=candidate_ids)
            .annotate(
                has_objective_facts=Exists(objective_facts),
                has_event_memberships=Exists(event_memberships),
            )
            .filter(has_objective_facts=False, has_event_memberships=False)
            .values_list("id", flat=True)
        )
        if not removable_ids:
            return 0
        NewsAnalysisResult.objects.filter(news_record_id__in=removable_ids).delete()
        deleted, _ = NewsRawRecord.objects.filter(id__in=removable_ids).delete()
    return deleted


def _candidate_queryset(
    *, mode: str, analysis_version: str, record_ids: list[int] | None
) -> QuerySet[NewsRawRecord]:
    current_results = NewsAnalysisResult.objects.filter(
        news_record_id=OuterRef("pk"), analysis_version=analysis_version
    )
    successful_results = current_results.filter(status=NewsAnalysisResult.Status.SUCCESS)
    failed_results = current_results.filter(status=NewsAnalysisResult.Status.FAILED)
    queryset = NewsRawRecord.objects.select_related("source")
    if record_ids is not None:
        queryset = queryset.filter(id__in=record_ids)
    if mode in {NewsAnalysisRun.Mode.INCREMENTAL, NewsAnalysisRun.Mode.SMOKE}:
        queryset = queryset.annotate(has_success=Exists(successful_results)).filter(
            has_success=False
        )
    elif mode == NewsAnalysisRun.Mode.RETRY_FAILED:
        queryset = queryset.annotate(has_failure=Exists(failed_results)).filter(
            has_failure=True
        )
    else:
        raise ValueError("不支持的新闻分类运行模式。")
    return queryset.order_by("id")


def _create_run(*, trigger: str, mode: str, config: AnalysisConfig) -> NewsAnalysisRun:
    try:
        with transaction.atomic():
            return NewsAnalysisRun.objects.create(
                trigger=trigger,
                mode=mode,
                analysis_version=config.analysis_version,
                prompt_version=config.prompt_version,
                model_name=config.model,
                started_at=timezone.now(),
                status=NewsAnalysisRun.Status.RUNNING,
            )
    except IntegrityError as exc:
        if NewsAnalysisRun.objects.filter(
            analysis_version=config.analysis_version,
            status=NewsAnalysisRun.Status.RUNNING,
        ).exists():
            raise AnalysisAlreadyRunning("当前分类版本已有运行中的任务。") from exc
        raise


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _distributed_usage(usage: TokenUsage, count: int) -> list[TokenUsage]:
    if count <= 0:
        return []

    def distribute(total: int) -> list[int]:
        quotient, remainder = divmod(total, count)
        return [quotient + (1 if index < remainder else 0) for index in range(count)]

    inputs = distribute(usage.input_tokens)
    outputs = distribute(usage.output_tokens)
    totals = distribute(usage.total_tokens)
    return [TokenUsage(inputs[i], outputs[i], totals[i]) for i in range(count)]


def _decision_summary(decision: RuleDecision | AIItem, fallback: str = "") -> str:
    if isinstance(decision, AIItem) and decision.content_summary:
        return decision.content_summary
    return fallback


def _news_referenced_ids(news_record_ids: list[int]) -> set[int]:
    """Return ids referenced by evidence models whose PROTECT keys block deletion."""
    if not news_record_ids:
        return set()
    objective_fact_ids = ObjectiveFactExtractionResult.objects.filter(
        news_record_id__in=news_record_ids
    ).values_list("news_record_id", flat=True)
    event_membership_ids = EventMembership.objects.filter(
        news_record_id__in=news_record_ids
    ).values_list("news_record_id", flat=True)
    return set(objective_fact_ids) | set(event_membership_ids)


def _save_success_results(
    *,
    run: NewsAnalysisRun,
    records: list[NewsRawRecord],
    decisions: dict[int, RuleDecision | AIItem],
    stage: str,
    method: str,
    summaries: dict[int, str] | None = None,
    rule_ids: dict[int, str] | None = None,
    actual_model_name: str = "",
    usage: TokenUsage | None = None,
) -> tuple[int, int]:
    completed = 0
    skipped = 0
    per_item_usage = _distributed_usage(usage or TokenUsage(), len(records))
    now = timezone.now()
    with transaction.atomic():
        existing = {
            result.news_record_id: result
            for result in NewsAnalysisResult.objects.select_for_update().filter(
                news_record_id__in=[record.id for record in records],
                analysis_version=run.analysis_version,
            )
        }
        referenced_ids = _news_referenced_ids([record.id for record in records])
        for index, record in enumerate(records):
            decision = decisions[record.id]
            result = existing.get(record.id)
            if result and result.status == NewsAnalysisResult.Status.SUCCESS:
                skipped += 1
                continue
            if (
                decision.conclusion == NewsAnalysisResult.Conclusion.IRRELEVANT
                and record.id not in referenced_ids
            ):
                NewsAnalysisResult.objects.filter(news_record_id=record.id).delete()
                record.delete()
                completed += 1
                continue
            item_usage = per_item_usage[index]
            values = {
                "prompt_version": run.prompt_version,
                "status": NewsAnalysisResult.Status.SUCCESS,
                "conclusion": decision.conclusion,
                "classification_stage": stage,
                "rationale": decision.rationale,
                "content_summary": _decision_summary(
                    decision, (summaries or {}).get(record.id, record.summary)
                ),
                "method": method,
                "matched_rule_id": (rule_ids or {}).get(record.id, ""),
                "actual_model_name": (
                    actual_model_name if method == NewsAnalysisResult.Method.AI else ""
                ),
                "input_tokens": item_usage.input_tokens,
                "output_tokens": item_usage.output_tokens,
                "total_tokens": item_usage.total_tokens,
                "safe_error_summary": "",
                "analysis_run": run,
                "analyzed_at": now,
            }
            if result:
                for field, value in values.items():
                    setattr(result, field, value)
                result.full_clean(validate_unique=False, validate_constraints=False)
                result.save(update_fields=[*values, "updated_at"])
            else:
                result = NewsAnalysisResult(
                    news_record=record,
                    analysis_version=run.analysis_version,
                    **values,
                )
                result.full_clean(validate_unique=False, validate_constraints=False)
                result.save(force_insert=True)
            completed += 1
    return completed, skipped


def _save_failed_results(
    *,
    run: NewsAnalysisRun,
    records: list[NewsRawRecord],
    safe_summary: str,
    usage: TokenUsage | None = None,
) -> tuple[int, int]:
    saved = 0
    skipped = 0
    now = timezone.now()
    per_item_usage = _distributed_usage(usage or TokenUsage(), len(records))
    with transaction.atomic():
        existing = {
            result.news_record_id: result
            for result in NewsAnalysisResult.objects.select_for_update().filter(
                news_record_id__in=[record.id for record in records],
                analysis_version=run.analysis_version,
            )
        }
        for index, record in enumerate(records):
            result = existing.get(record.id)
            if result and result.status == NewsAnalysisResult.Status.SUCCESS:
                skipped += 1
                continue
            values = {
                "prompt_version": run.prompt_version,
                "status": NewsAnalysisResult.Status.FAILED,
                "conclusion": "",
                "classification_stage": "",
                "rationale": "",
                "content_summary": "",
                "method": "",
                "matched_rule_id": "",
                "actual_model_name": "",
                "input_tokens": per_item_usage[index].input_tokens,
                "output_tokens": per_item_usage[index].output_tokens,
                "total_tokens": per_item_usage[index].total_tokens,
                "safe_error_summary": safe_summary[:500],
                "analysis_run": run,
                "analyzed_at": now,
            }
            if result:
                for field, value in values.items():
                    setattr(result, field, value)
                result.full_clean(validate_unique=False, validate_constraints=False)
                result.save(update_fields=[*values, "updated_at"])
            else:
                result = NewsAnalysisResult(
                    news_record=record,
                    analysis_version=run.analysis_version,
                    **values,
                )
                result.full_clean(validate_unique=False, validate_constraints=False)
                result.save(force_insert=True)
            saved += 1
    return saved, skipped


def _sync_run(run: NewsAnalysisRun) -> None:
    run.save(
        update_fields=[
            "rule_processed_count",
            "ai_processed_count",
            "success_count",
            "failure_count",
            "skipped_count",
            "api_request_count",
            "retry_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "safe_error_summary",
            "updated_at",
        ]
    )


def _finish_run(run: NewsAnalysisRun) -> NewsAnalysisRun:
    if run.failure_count:
        run.status = NewsAnalysisRun.Status.PARTIAL if run.success_count else NewsAnalysisRun.Status.FAILED
    elif run.skipped_count and not run.success_count:
        run.status = NewsAnalysisRun.Status.FAILED
    elif run.skipped_count:
        run.status = NewsAnalysisRun.Status.PARTIAL
    else:
        run.status = NewsAnalysisRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "safe_error_summary", "updated_at"])
    return run


def _load_contents(
    records: list[NewsRawRecord],
    loader: Callable[[NewsRawRecord], ArticleContent | str],
) -> dict[int, str]:
    contents: dict[int, str] = {}
    for record in records:
        try:
            loaded = loader(record)
        except Exception:
            text = record.summary
        else:
            text = loaded.text if isinstance(loaded, ArticleContent) else str(loaded)
        contents[record.id] = text or record.summary or ""
    return contents


def _load_analysis_contents(
    records: list[NewsRawRecord],
    loader: Callable[[NewsRawRecord], ArticleContent | str],
) -> dict[int, str]:
    """Use saved feed/list summaries for sources that do not permit body loading."""
    summary_only_records = [
        record for record in records if record.source.code in SUMMARY_ONLY_SOURCE_CODES
    ]
    article_records = [
        record
        for record in records
        if record.source.code not in SUMMARY_ONLY_SOURCE_CODES
    ]
    contents = {record.id: record.summary or "" for record in summary_only_records}
    contents.update(_load_contents(article_records, loader))
    return contents


def _summary_map(records: list[NewsRawRecord], contents: dict[int, str]) -> dict[int, str]:
    return {
        record.id: summarize_article_text(contents.get(record.id, "")) or record.summary
        for record in records
    }


def run_news_analysis(
    *,
    mode: str = NewsAnalysisRun.Mode.INCREMENTAL,
    trigger: str = NewsAnalysisRun.Trigger.MANUAL,
    record_ids: list[int] | None = None,
    client: DeepSeekNewsClient | None = None,
    article_loader: Callable[[NewsRawRecord], ArticleContent | str] = fetch_source_article,
    max_requests: int | None = None,
) -> NewsAnalysisRun:
    config = get_analysis_config()
    if max_requests is not None:
        config = replace(config, max_requests_per_run=max(1, max_requests))
    prune_expired_news()
    run = _create_run(trigger=trigger, mode=mode, config=config)
    try:
        candidates = list(
            _candidate_queryset(
                mode=mode,
                analysis_version=config.analysis_version,
                record_ids=record_ids,
            )
        )
        run.candidate_count = len(candidates)
        run.save(update_fields=["candidate_count", "updated_at"])
        if not candidates:
            return _finish_run(run)
        if client is None and not config.api_key:
            run.status = NewsAnalysisRun.Status.NOT_RUN
            run.skipped_count = len(candidates)
            run.safe_error_summary = "DeepSeek API 未配置，ETH 新闻分类未执行。"
            run.finished_at = timezone.now()
            run.save(
                update_fields=[
                    "status",
                    "skipped_count",
                    "safe_error_summary",
                    "finished_at",
                    "updated_at",
                ]
            )
            return run

        rule_items: list[tuple[NewsRawRecord, RuleDecision]] = []
        title_ai_records: list[NewsRawRecord] = []
        for record in candidates:
            decision = match_fixed_rule(record)
            if decision:
                rule_items.append((record, decision))
            else:
                title_ai_records.append(record)

        for rule_batch in _chunks(rule_items, config.batch_size):
            records = [item[0] for item in rule_batch]
            decisions = {item[0].id: item[1] for item in rule_batch}
            relevant = [
                record
                for record in records
                if decisions[record.id].conclusion != NewsAnalysisResult.Conclusion.IRRELEVANT
            ]
            contents = _load_analysis_contents(relevant, article_loader)
            completed, skipped = _save_success_results(
                run=run,
                records=records,
                decisions=decisions,
                stage=NewsAnalysisResult.ClassificationStage.TITLE_RULE,
                method=NewsAnalysisResult.Method.RULE,
                summaries=_summary_map(relevant, contents),
                rule_ids={item[0].id: item[1].rule_id for item in rule_batch},
            )
            run.rule_processed_count += len(records)
            run.success_count += completed
            run.skipped_count += skipped
            _sync_run(run)

        ai_client = client or DeepSeekNewsClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
        )
        content_records: list[NewsRawRecord] = []
        fatal_error = False

        for batch_index, records in enumerate(_chunks(title_ai_records, config.batch_size)):
            remaining_requests = config.max_requests_per_run - run.api_request_count
            if remaining_requests <= 0 or fatal_error:
                run.skipped_count += len(title_ai_records[batch_index * config.batch_size :])
                run.safe_error_summary = (
                    "本次运行已达到 API 请求上限。"
                    if remaining_requests <= 0
                    else "因不可重试的 AI 配置或服务错误，已停止后续批次。"
                )
                _sync_run(run)
                break
            run.ai_processed_count += len(records)
            try:
                batch = ai_client.analyze_batch(
                    records,
                    max_requests=remaining_requests,
                    stage=NewsAnalysisResult.ClassificationStage.TITLE_AI,
                )
            except BatchAnalysisError as exc:
                run.api_request_count += exc.request_count
                run.retry_count += exc.retry_count
                run.input_tokens += exc.usage.input_tokens
                run.output_tokens += exc.usage.output_tokens
                run.total_tokens += exc.usage.total_tokens
                saved, skipped = _save_failed_results(
                    run=run, records=records, safe_summary=exc.safe_summary, usage=exc.usage
                )
                run.failure_count += saved
                run.skipped_count += skipped
                run.safe_error_summary = exc.safe_summary
                fatal_error = exc.fatal
            else:
                run.api_request_count += batch.request_count
                run.retry_count += batch.retry_count
                run.input_tokens += batch.usage.input_tokens
                run.output_tokens += batch.usage.output_tokens
                run.total_tokens += batch.usage.total_tokens
                decisions = {item.news_id: item for item in batch.items}
                unclear = [
                    record
                    for record in records
                    if decisions[record.id].conclusion == NewsAnalysisResult.Conclusion.UNCLEAR
                ]
                content_records.extend(unclear)
                resolved = [record for record in records if record not in unclear]
                relevant = [
                    record
                    for record in resolved
                    if decisions[record.id].conclusion != NewsAnalysisResult.Conclusion.IRRELEVANT
                ]
                contents = _load_analysis_contents(relevant, article_loader)
                if resolved:
                    completed, skipped = _save_success_results(
                        run=run,
                        records=resolved,
                        decisions=decisions,
                        stage=NewsAnalysisResult.ClassificationStage.TITLE_AI,
                        method=NewsAnalysisResult.Method.AI,
                        summaries=_summary_map(relevant, contents),
                        actual_model_name=batch.actual_model_name,
                        usage=batch.usage,
                    )
                    run.success_count += completed
                    run.skipped_count += skipped
            _sync_run(run)

        if not fatal_error:
            summary_records = [
                record
                for record in content_records
                if record.source.code in SUMMARY_ONLY_SOURCE_CODES
            ]
            article_records = [
                record
                for record in content_records
                if record.source.code not in SUMMARY_ONLY_SOURCE_CODES
            ]
            detail_groups = (
                (NewsAnalysisResult.ClassificationStage.SUMMARY_AI, summary_records),
                (NewsAnalysisResult.ClassificationStage.CONTENT_AI, article_records),
            )
            for detail_stage, detail_records in detail_groups:
                if fatal_error:
                    break
                for batch_index, records in enumerate(
                    _chunks(detail_records, config.batch_size)
                ):
                    remaining_requests = config.max_requests_per_run - run.api_request_count
                    if remaining_requests <= 0:
                        run.skipped_count += len(
                            detail_records[batch_index * config.batch_size :]
                        )
                        run.safe_error_summary = "本次运行已达到 API 请求上限。"
                        _sync_run(run)
                        break
                    if detail_stage == NewsAnalysisResult.ClassificationStage.SUMMARY_AI:
                        contents = {
                            record.id: record.summary or "" for record in records
                        }
                    else:
                        contents = _load_contents(records, article_loader)
                    run.ai_processed_count += len(records)
                    try:
                        batch = ai_client.analyze_batch(
                            records,
                            max_requests=remaining_requests,
                            stage=detail_stage,
                            contents=contents,
                        )
                    except BatchAnalysisError as exc:
                        run.api_request_count += exc.request_count
                        run.retry_count += exc.retry_count
                        run.input_tokens += exc.usage.input_tokens
                        run.output_tokens += exc.usage.output_tokens
                        run.total_tokens += exc.usage.total_tokens
                        saved, skipped = _save_failed_results(
                            run=run,
                            records=records,
                            safe_summary=exc.safe_summary,
                            usage=exc.usage,
                        )
                        run.failure_count += saved
                        run.skipped_count += skipped
                        run.safe_error_summary = exc.safe_summary
                        fatal_error = exc.fatal
                    else:
                        run.api_request_count += batch.request_count
                        run.retry_count += batch.retry_count
                        run.input_tokens += batch.usage.input_tokens
                        run.output_tokens += batch.usage.output_tokens
                        run.total_tokens += batch.usage.total_tokens
                        decisions = {item.news_id: item for item in batch.items}
                        completed, skipped = _save_success_results(
                            run=run,
                            records=records,
                            decisions=decisions,
                            stage=detail_stage,
                            method=NewsAnalysisResult.Method.AI,
                            summaries=_summary_map(records, contents),
                            actual_model_name=batch.actual_model_name,
                            usage=batch.usage,
                        )
                        run.success_count += completed
                        run.skipped_count += skipped
                    _sync_run(run)
                    if fatal_error:
                        break

        prune_expired_news()
        return _finish_run(run)
    except Exception:
        run.status = NewsAnalysisRun.Status.FAILED
        run.finished_at = timezone.now()
        if not run.safe_error_summary:
            run.safe_error_summary = "新闻分类运行发生内部错误。"
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "safe_error_summary",
                "updated_at",
            ]
        )
        raise AnalysisExecutionFailed(run)
