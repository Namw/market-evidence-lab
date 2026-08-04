from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.news_data.models import NewsRawRecord

from .models import ObjectiveFactExtractionResult, ObjectiveFactExtractionRun
from .objective_fact_validation import (
    GENERATION_PARAMETERS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    DeepSeekObjectiveFactClient,
    build_article_input,
    build_request_payload,
)


PROVIDER = "DeepSeek"
CONCURRENCY_SLOT = "objective_fact"
STALE_RUN_AFTER = timedelta(minutes=15)
BATCH_MODES = {
    ObjectiveFactExtractionRun.Mode.INCREMENTAL,
    ObjectiveFactExtractionRun.Mode.RETRY_FAILED,
}
SINGLE_MODES = {
    ObjectiveFactExtractionRun.Mode.SINGLE,
    ObjectiveFactExtractionRun.Mode.RETRY_SINGLE,
    ObjectiveFactExtractionRun.Mode.REEXTRACT,
}
ALL_MODES = BATCH_MODES | SINGLE_MODES


class ObjectiveFactAlreadyRunning(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ObjectiveFactConfig:
    provider: str
    model: str
    prompt_version: str
    generation_parameters: dict


def get_objective_fact_config(*, prompt_version: str | None = None) -> ObjectiveFactConfig:
    configured = getattr(settings, "NEWS_OBJECTIVE_FACT_PROMPT_VERSION", PROMPT_VERSION)
    return ObjectiveFactConfig(
        provider=PROVIDER,
        model=settings.NEWS_AI_MODEL,
        prompt_version=prompt_version or configured,
        generation_parameters=dict(GENERATION_PARAMETERS),
    )


def is_retryable_objective_fact_result(
    result: ObjectiveFactExtractionResult | None,
) -> bool:
    """Return whether the latest result is an invalid execution worth retrying."""
    if result is None:
        return False
    return (
        result.extraction_status != ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS
        or not result.ai_call_succeeded
        or not result.json_parse_succeeded
        or result.validation_status == ObjectiveFactExtractionResult.ValidationStatus.ERROR
    )


def objective_fact_single_mode(
    result: ObjectiveFactExtractionResult | None,
) -> str:
    if result is None:
        return ObjectiveFactExtractionRun.Mode.SINGLE
    if is_retryable_objective_fact_result(result):
        return ObjectiveFactExtractionRun.Mode.RETRY_SINGLE
    return ObjectiveFactExtractionRun.Mode.REEXTRACT


def _latest_result_map(
    records: list[NewsRawRecord], prompt_version: str
) -> dict[int, ObjectiveFactExtractionResult]:
    record_ids = [record.id for record in records]
    if not record_ids:
        return {}
    latest: dict[int, ObjectiveFactExtractionResult] = {}
    results = ObjectiveFactExtractionResult.objects.filter(
        prompt_version=prompt_version,
        news_record_id__in=record_ids,
    ).only(
        "id",
        "news_record_id",
        "extraction_status",
        "validation_status",
        "ai_call_succeeded",
        "json_parse_succeeded",
    ).order_by("news_record_id", "-extracted_at", "-id")
    for result in results:
        latest.setdefault(result.news_record_id, result)
    return latest


def _validate_mode_scope(mode: str, record_ids: list[int] | None) -> None:
    if mode not in ALL_MODES:
        raise ValueError("不支持的客观事实提取运行模式。")
    if mode in SINGLE_MODES and (record_ids is None or len(set(record_ids)) != 1):
        raise ValueError("单条客观事实提取必须且只能指定一篇新闻。")


def _selection(
    *, mode: str, prompt_version: str, record_ids: list[int] | None
) -> tuple[list[NewsRawRecord], int]:
    _validate_mode_scope(mode, record_ids)
    scope = NewsRawRecord.objects.select_related("source").order_by("id")
    if record_ids is not None:
        scope = scope.filter(id__in=record_ids)
    records = list(scope)
    if mode in SINGLE_MODES and len(records) != 1:
        raise ValueError("指定的新闻不存在。")

    record_id_values = [record.id for record in records]
    latest = _latest_result_map(records, prompt_version)
    successful_ids = set(
        ObjectiveFactExtractionResult.objects.filter(
            prompt_version=prompt_version,
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS,
            news_record_id__in=record_id_values,
        ).values_list("news_record_id", flat=True)
    )
    if mode == ObjectiveFactExtractionRun.Mode.INCREMENTAL:
        candidates = [record for record in records if record.id not in successful_ids]
    elif mode == ObjectiveFactExtractionRun.Mode.RETRY_FAILED:
        candidates = [
            record
            for record in records
            if is_retryable_objective_fact_result(latest.get(record.id))
        ]
    elif mode == ObjectiveFactExtractionRun.Mode.SINGLE:
        if latest.get(records[0].id) is not None:
            raise ValueError("该新闻已有当前版本结果，请使用重新提取。")
        candidates = records
    elif mode == ObjectiveFactExtractionRun.Mode.RETRY_SINGLE:
        if not is_retryable_objective_fact_result(latest.get(records[0].id)):
            raise ValueError("该新闻当前最新结果不属于可重试失败。")
        candidates = records
    else:
        if latest.get(records[0].id) is None:
            raise ValueError("该新闻尚无当前版本结果，请先执行单条提取。")
        candidates = records
    return candidates, len(records) - len(candidates)


def objective_fact_selection_count(
    *,
    mode: str,
    record_ids: list[int] | None = None,
    prompt_version: str | None = None,
) -> tuple[int, int]:
    config = get_objective_fact_config(prompt_version=prompt_version)
    candidates, skipped = _selection(
        mode=mode,
        prompt_version=config.prompt_version,
        record_ids=record_ids,
    )
    return len(candidates), skipped


def _recover_stale_runs(now) -> None:
    stale_runs = list(
        ObjectiveFactExtractionRun.objects.select_for_update().filter(
            status=ObjectiveFactExtractionRun.Status.RUNNING,
            updated_at__lt=now - STALE_RUN_AFTER,
        )
    )
    for stale in stale_runs:
        issue = {
            "code": "OBJECTIVE_FACT_RUN_INTERRUPTED",
            "message": "客观事实提取运行异常中断，已由后续请求关闭。",
        }
        stale.results.filter(
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.PENDING
        ).update(
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
            validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
            validation_errors=[issue],
            safe_error_summary=issue["message"],
            extracted_at=now,
            updated_at=now,
        )
        _sync_run(stale)
        stale.status = ObjectiveFactExtractionRun.Status.FAILED
        stale.safe_error_summary = issue["message"]
        stale.finished_at = now
        stale.save(
            update_fields=[
                "status",
                "safe_error_summary",
                "finished_at",
                "updated_at",
            ]
        )


def _create_run(
    *,
    trigger: str,
    triggered_by: str,
    mode: str,
    config: ObjectiveFactConfig,
) -> ObjectiveFactExtractionRun:
    try:
        with transaction.atomic():
            now = timezone.now()
            _recover_stale_runs(now)
            return ObjectiveFactExtractionRun.objects.create(
                trigger=trigger,
                triggered_by=triggered_by[:150],
                mode=mode,
                status=ObjectiveFactExtractionRun.Status.RUNNING,
                provider=config.provider,
                model=config.model,
                prompt_version=config.prompt_version,
                generation_parameters=config.generation_parameters,
                concurrency_slot=CONCURRENCY_SLOT,
                started_at=now,
            )
    except IntegrityError as exc:
        if ObjectiveFactExtractionRun.objects.filter(
            concurrency_slot=CONCURRENCY_SLOT,
            status=ObjectiveFactExtractionRun.Status.RUNNING,
        ).exists():
            raise ObjectiveFactAlreadyRunning(
                "当前已有客观事实提取任务正在运行，请勿重复启动。"
            ) from exc
        raise


def _pending_result(
    *,
    run: ObjectiveFactExtractionRun,
    record: NewsRawRecord,
    config: ObjectiveFactConfig,
) -> tuple[ObjectiveFactExtractionResult, object]:
    article = build_article_input(record)
    _, user_prompt = build_request_payload(article, config.model)
    has_body = bool(article.stored_body)
    scope_note = (
        "本次提取使用数据库保存的标题、摘要和正文。"
        if has_body
        else "本次提取仅使用数据库保存的标题和摘要，未读取新闻全文。"
    )
    result = ObjectiveFactExtractionResult.objects.create(
        news_record=record,
        extraction_run=run,
        extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.PENDING,
        validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
        provider=config.provider,
        model=config.model,
        prompt_version=config.prompt_version,
        generation_parameters=config.generation_parameters,
        input_snapshot={
            "news_id": article.news_id,
            "source": article.source,
            "title": article.title,
            "summary": article.summary,
            "stored_body": article.stored_body,
            "published_at": article.published_at,
            "source_category": article.source_category,
            "source_tags": article.source_tags,
            "source_author": article.source_author,
        },
        has_stored_body=has_body,
        stored_body_included=has_body,
        input_scope_note=scope_note,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )
    return result, article


def _preparation_failure_result(
    *,
    run: ObjectiveFactExtractionRun,
    record: NewsRawRecord,
    config: ObjectiveFactConfig,
    exc: Exception,
) -> ObjectiveFactExtractionResult:
    issue = {
        "code": "OBJECTIVE_FACT_INPUT_ERROR",
        "message": f"客观事实输入准备失败：{type(exc).__name__}。",
    }
    return ObjectiveFactExtractionResult.objects.create(
        news_record=record,
        extraction_run=run,
        extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
        validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
        provider=config.provider,
        model=config.model,
        prompt_version=config.prompt_version,
        generation_parameters=config.generation_parameters,
        input_snapshot={
            "news_id": record.id,
            "title": record.title,
            "summary": record.summary,
        },
        input_scope_note="输入准备失败，未调用模型。",
        system_prompt=SYSTEM_PROMPT,
        user_prompt="",
        validation_errors=[issue],
        safe_error_summary=issue["message"],
        extracted_at=timezone.now(),
    )


def _first_error(validation: dict) -> str:
    errors = validation.get("errors") if isinstance(validation, dict) else None
    if isinstance(errors, list) and errors:
        issue = errors[0]
        if isinstance(issue, dict):
            return str(issue.get("message") or issue.get("code") or "客观事实提取失败。")[:500]
        return str(issue)[:500]
    return ""


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _token_usage(response: object) -> tuple[int, int, int]:
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0
    prompt = _nonnegative_int(usage.get("prompt_tokens"))
    completion = _nonnegative_int(usage.get("completion_tokens"))
    total = _nonnegative_int(usage.get("total_tokens")) or prompt + completion
    return prompt, completion, total


def _save_processed_result(
    result: ObjectiveFactExtractionResult, processed: dict, config: ObjectiveFactConfig
) -> None:
    processing_status = processed.get("processing_status")
    ai_succeeded = processing_status in {
        "json_parse_failed",
        "validation_failed",
        "success",
    }
    json_succeeded = processing_status in {"validation_failed", "success"}
    validation = processed.get("validation")
    if not isinstance(validation, dict):
        validation = {"errors": [], "warnings": [], "evidence_matches": []}
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []
    matches = (
        validation.get("evidence_matches")
        if isinstance(validation.get("evidence_matches"), list)
        else []
    )
    if not json_succeeded or errors:
        validation_status = ObjectiveFactExtractionResult.ValidationStatus.ERROR
    elif warnings:
        validation_status = ObjectiveFactExtractionResult.ValidationStatus.WARNING
    else:
        validation_status = ObjectiveFactExtractionResult.ValidationStatus.PASSED
    parsed = processed.get("parsed_result")
    facts = parsed.get("facts") if isinstance(parsed, dict) else []
    if not isinstance(facts, list):
        facts = []
    response = processed.get("api_response")
    actual_model = response.get("model") if isinstance(response, dict) else None
    prompt_tokens, completion_tokens, total_tokens = _token_usage(response)
    result.extraction_status = (
        ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS
        if json_succeeded
        else ObjectiveFactExtractionResult.ExtractionStatus.FAILED
    )
    result.validation_status = validation_status
    result.ai_call_succeeded = ai_succeeded
    result.json_parse_succeeded = json_succeeded
    result.model = actual_model if isinstance(actual_model, str) else config.model
    result.api_response = response
    result.raw_model_output = processed.get("raw_model_output") or ""
    result.parsed_result = parsed
    result.json_parse_error = processed.get("json_parse_error") or ""
    result.validation_errors = errors
    result.validation_warnings = warnings
    result.evidence_matches = matches
    result.safe_error_summary = _first_error(validation)
    result.objective_summary = (
        parsed.get("objective_summary")
        if isinstance(parsed, dict) and isinstance(parsed.get("objective_summary"), str)
        else ""
    )
    result.event_status = (
        parsed.get("event_status")
        if isinstance(parsed, dict) and isinstance(parsed.get("event_status"), str)
        else ""
    )
    result.information_completeness = (
        parsed.get("information_completeness")
        if isinstance(parsed, dict)
        and isinstance(parsed.get("information_completeness"), str)
        else ""
    )
    result.facts_count = len(facts)
    result.prompt_tokens = prompt_tokens
    result.completion_tokens = completion_tokens
    result.total_tokens = total_tokens
    result.extracted_at = timezone.now()
    result.save()


def _save_unexpected_failure(
    result: ObjectiveFactExtractionResult, exc: Exception
) -> None:
    issue = {
        "code": "OBJECTIVE_FACT_INTERNAL_ERROR",
        "message": f"客观事实提取发生内部错误：{type(exc).__name__}。",
    }
    result.extraction_status = ObjectiveFactExtractionResult.ExtractionStatus.FAILED
    result.validation_status = ObjectiveFactExtractionResult.ValidationStatus.ERROR
    result.validation_errors = [issue]
    result.safe_error_summary = issue["message"]
    result.extracted_at = timezone.now()
    result.save()


def _sync_run(run: ObjectiveFactExtractionRun) -> None:
    results = run.results.all()
    totals = results.aggregate(
        processed=Count(
            "id",
            filter=~Q(
                extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.PENDING
            ),
        ),
        requests=Sum("request_count"),
        successes=Count(
            "id",
            filter=Q(
                extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS
            ),
        ),
        failures=Count(
            "id",
            filter=Q(
                extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED
            ),
        ),
        passed=Count(
            "id",
            filter=Q(
                validation_status=ObjectiveFactExtractionResult.ValidationStatus.PASSED
            ),
        ),
        warnings=Count(
            "id",
            filter=Q(
                validation_status=ObjectiveFactExtractionResult.ValidationStatus.WARNING
            ),
        ),
        errors=Count(
            "id",
            filter=Q(
                validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR
            ),
        ),
        facts=Sum("facts_count"),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        total_tokens=Sum("total_tokens"),
    )
    run.processed_count = totals["processed"] or 0
    run.request_count = totals["requests"] or 0
    run.success_count = totals["successes"] or 0
    run.failed_count = totals["failures"] or 0
    run.validation_passed_count = totals["passed"] or 0
    run.validation_warning_count = totals["warnings"] or 0
    run.validation_error_count = totals["errors"] or 0
    run.facts_count = totals["facts"] or 0
    run.prompt_tokens = totals["prompt_tokens"] or 0
    run.completion_tokens = totals["completion_tokens"] or 0
    run.total_tokens = totals["total_tokens"] or 0
    run.save(
        update_fields=[
            "processed_count",
            "request_count",
            "success_count",
            "failed_count",
            "validation_passed_count",
            "validation_warning_count",
            "validation_error_count",
            "facts_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "safe_error_summary",
            "updated_at",
        ]
    )


def _finish_run(run: ObjectiveFactExtractionRun) -> ObjectiveFactExtractionRun:
    valid_count = run.validation_passed_count + run.validation_warning_count
    if run.validation_error_count and valid_count:
        run.status = ObjectiveFactExtractionRun.Status.PARTIAL
    elif run.validation_error_count:
        run.status = ObjectiveFactExtractionRun.Status.FAILED
    else:
        run.status = ObjectiveFactExtractionRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "safe_error_summary", "updated_at"])
    return run


def _fail_run(run: ObjectiveFactExtractionRun, exc: Exception) -> ObjectiveFactExtractionRun:
    issue = {
        "code": "OBJECTIVE_FACT_RUN_ERROR",
        "message": f"客观事实提取运行失败：{type(exc).__name__}。",
    }
    run.results.filter(
        extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.PENDING
    ).update(
        extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
        validation_status=ObjectiveFactExtractionResult.ValidationStatus.ERROR,
        validation_errors=[issue],
        safe_error_summary=issue["message"],
        extracted_at=timezone.now(),
        updated_at=timezone.now(),
    )
    _sync_run(run)
    run.status = ObjectiveFactExtractionRun.Status.FAILED
    run.safe_error_summary = issue["message"]
    run.finished_at = timezone.now()
    run.save(
        update_fields=["status", "safe_error_summary", "finished_at", "updated_at"]
    )
    return run


def run_objective_fact_extraction(
    *,
    mode: str = ObjectiveFactExtractionRun.Mode.INCREMENTAL,
    trigger: str = ObjectiveFactExtractionRun.Trigger.COMMAND,
    triggered_by: str = "",
    record_ids: list[int] | None = None,
    prompt_version: str | None = None,
    client: DeepSeekObjectiveFactClient | None = None,
) -> ObjectiveFactExtractionRun:
    _validate_mode_scope(mode, record_ids)
    config = get_objective_fact_config(prompt_version=prompt_version)
    run = _create_run(
        trigger=trigger,
        triggered_by=triggered_by,
        mode=mode,
        config=config,
    )
    try:
        candidates, skipped = _selection(
            mode=mode,
            prompt_version=config.prompt_version,
            record_ids=record_ids,
        )
        run.candidate_count = len(candidates)
        run.skipped_count = skipped
        run.save(update_fields=["candidate_count", "skipped_count", "updated_at"])
        if not candidates:
            return _finish_run(run)
        if client is None and not settings.NEWS_AI_API_KEY:
            run.status = ObjectiveFactExtractionRun.Status.NOT_RUN
            run.skipped_count += len(candidates)
            run.safe_error_summary = "DeepSeek API 未配置，客观事实提取未执行。"
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

        ai_client = client or DeepSeekObjectiveFactClient()
        owns_client = client is None
        try:
            for record in candidates:
                try:
                    result, article = _pending_result(
                        run=run, record=record, config=config
                    )
                except Exception as exc:
                    result = _preparation_failure_result(
                        run=run,
                        record=record,
                        config=config,
                        exc=exc,
                    )
                    run.safe_error_summary = result.safe_error_summary
                    _sync_run(run)
                    continue
                result.request_count = 1
                result.save(update_fields=["request_count", "updated_at"])
                try:
                    processed = ai_client.process(article)
                    _save_processed_result(result, processed, config)
                except Exception as exc:
                    _save_unexpected_failure(result, exc)
                if result.safe_error_summary:
                    run.safe_error_summary = result.safe_error_summary
                _sync_run(run)
        finally:
            if owns_client:
                ai_client.close()
        return _finish_run(run)
    except Exception as exc:
        return _fail_run(run, exc)
