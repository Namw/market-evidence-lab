from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from apps.news_data.models import NewsRawRecord

from .ai import AIItem, BatchAnalysisError, DeepSeekNewsClient, TokenUsage
from .models import NewsAnalysisResult, NewsAnalysisRun
from .rules import RuleDecision, match_fixed_rule


class AnalysisAlreadyRunning(Exception):
    pass


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


def _candidate_queryset(
    *, mode: str, analysis_version: str, record_ids: list[int] | None
) -> QuerySet[NewsRawRecord]:
    current_results = NewsAnalysisResult.objects.filter(
        news_record_id=OuterRef("pk"), analysis_version=analysis_version
    )
    successful_results = current_results.filter(
        status=NewsAnalysisResult.Status.SUCCESS
    )
    failed_results = current_results.filter(status=NewsAnalysisResult.Status.FAILED)
    queryset = NewsRawRecord.objects.select_related("source")
    if record_ids is not None:
        queryset = queryset.filter(id__in=record_ids)
    if mode in {
        NewsAnalysisRun.Mode.INCREMENTAL,
        NewsAnalysisRun.Mode.SMOKE,
    }:
        queryset = queryset.annotate(
            has_success=Exists(successful_results)
        ).filter(has_success=False)
    elif mode == NewsAnalysisRun.Mode.RETRY_FAILED:
        queryset = queryset.annotate(has_failure=Exists(failed_results)).filter(
            has_failure=True
        )
    else:
        raise ValueError("不支持的新闻分析运行模式。")
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
            raise AnalysisAlreadyRunning("当前分析版本已有运行中的任务。") from exc
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


def _save_success_results(
    *,
    run: NewsAnalysisRun,
    records: list[NewsRawRecord],
    decisions: dict[int, RuleDecision | AIItem],
    method: str,
    rule_ids: dict[int, str] | None = None,
    actual_model_name: str = "",
    usage: TokenUsage | None = None,
) -> tuple[int, int]:
    saved = 0
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
        for index, record in enumerate(records):
            result = existing.get(record.id)
            if result and result.status == NewsAnalysisResult.Status.SUCCESS:
                skipped += 1
                continue
            decision = decisions[record.id]
            item_usage = per_item_usage[index]
            values = {
                "prompt_version": run.prompt_version,
                "status": NewsAnalysisResult.Status.SUCCESS,
                "observation_result": decision.observation_result,
                "event_type": decision.event_type,
                "impact_scope": decision.impact_scope,
                "importance": decision.importance,
                "rationale": decision.rationale,
                "confidence": decision.confidence,
                "method": method,
                "matched_rule_id": (rule_ids or {}).get(record.id, ""),
                "actual_model_name": actual_model_name if method == NewsAnalysisResult.Method.AI else "",
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
            saved += 1
    return saved, skipped


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
                "observation_result": "",
                "event_type": "",
                "impact_scope": "",
                "importance": "",
                "rationale": "",
                "confidence": "",
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
        run.status = (
            NewsAnalysisRun.Status.PARTIAL
            if run.success_count
            else NewsAnalysisRun.Status.FAILED
        )
    elif run.skipped_count and not run.success_count:
        run.status = NewsAnalysisRun.Status.FAILED
    elif run.skipped_count:
        run.status = NewsAnalysisRun.Status.PARTIAL
    else:
        run.status = NewsAnalysisRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "safe_error_summary",
            "updated_at",
        ]
    )
    return run


def run_news_analysis(
    *,
    mode: str = NewsAnalysisRun.Mode.INCREMENTAL,
    trigger: str = NewsAnalysisRun.Trigger.MANUAL,
    record_ids: list[int] | None = None,
    client: DeepSeekNewsClient | None = None,
) -> NewsAnalysisRun:
    config = get_analysis_config()
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

        rule_items: list[tuple[NewsRawRecord, RuleDecision]] = []
        ai_records: list[NewsRawRecord] = []
        for record in candidates:
            decision = match_fixed_rule(record)
            if decision:
                rule_items.append((record, decision))
            else:
                ai_records.append(record)

        for rule_batch in _chunks(rule_items, config.batch_size):
            records = [item[0] for item in rule_batch]
            decisions = {item[0].id: item[1] for item in rule_batch}
            rule_ids = {item[0].id: item[1].rule_id for item in rule_batch}
            saved, skipped = _save_success_results(
                run=run,
                records=records,
                decisions=decisions,
                method=NewsAnalysisResult.Method.RULE,
                rule_ids=rule_ids,
            )
            run.rule_processed_count += saved
            run.success_count += saved
            run.skipped_count += skipped
            _sync_run(run)

        ai_client = client or DeepSeekNewsClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
        )
        fatal_error = False
        for batch_index, records in enumerate(_chunks(ai_records, config.batch_size)):
            remaining_requests = config.max_requests_per_run - run.api_request_count
            if remaining_requests <= 0 or fatal_error:
                remaining_batches = ai_records[batch_index * config.batch_size :]
                run.skipped_count += len(remaining_batches)
                if not run.safe_error_summary:
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
                    records, max_requests=remaining_requests
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
                saved, skipped = _save_success_results(
                    run=run,
                    records=records,
                    decisions=decisions,
                    method=NewsAnalysisResult.Method.AI,
                    actual_model_name=batch.actual_model_name,
                    usage=batch.usage,
                )
                run.success_count += saved
                run.skipped_count += skipped
            _sync_run(run)
        return _finish_run(run)
    except Exception:
        run.status = NewsAnalysisRun.Status.FAILED
        run.finished_at = timezone.now()
        if not run.safe_error_summary:
            run.safe_error_summary = "新闻分析运行发生内部错误。"
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "safe_error_summary",
                "updated_at",
            ]
        )
        raise
