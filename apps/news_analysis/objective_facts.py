from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
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


def _create_run(
    *, trigger: str, mode: str, config: ObjectiveFactConfig
) -> ObjectiveFactExtractionRun:
    try:
        with transaction.atomic():
            return ObjectiveFactExtractionRun.objects.create(
                trigger=trigger,
                mode=mode,
                status=ObjectiveFactExtractionRun.Status.RUNNING,
                provider=config.provider,
                model=config.model,
                prompt_version=config.prompt_version,
                generation_parameters=config.generation_parameters,
                started_at=timezone.now(),
            )
    except IntegrityError as exc:
        if ObjectiveFactExtractionRun.objects.filter(
            prompt_version=config.prompt_version,
            status=ObjectiveFactExtractionRun.Status.RUNNING,
        ).exists():
            raise ObjectiveFactAlreadyRunning(
                "当前客观事实提示词版本已有运行中的任务。"
            ) from exc
        raise


def _selection(
    *, mode: str, prompt_version: str, record_ids: list[int] | None
) -> tuple[list[NewsRawRecord], int]:
    scope = NewsRawRecord.objects.select_related("source").order_by("id")
    if record_ids is not None:
        scope = scope.filter(id__in=record_ids)
    records = list(scope)
    successful_ids = set(
        ObjectiveFactExtractionResult.objects.filter(
            prompt_version=prompt_version,
            extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS,
            news_record_id__in=[record.id for record in records],
        ).values_list("news_record_id", flat=True)
    )
    if mode == ObjectiveFactExtractionRun.Mode.INCREMENTAL:
        candidates = [record for record in records if record.id not in successful_ids]
    elif mode == ObjectiveFactExtractionRun.Mode.RETRY_FAILED:
        failed_ids = set(
            ObjectiveFactExtractionResult.objects.filter(
                prompt_version=prompt_version,
                extraction_status=ObjectiveFactExtractionResult.ExtractionStatus.FAILED,
                news_record_id__in=[record.id for record in records],
            ).values_list("news_record_id", flat=True)
        )
        candidates = [
            record
            for record in records
            if record.id in failed_ids and record.id not in successful_ids
        ]
    else:
        raise ValueError("不支持的客观事实提取运行模式。")
    return candidates, len(records) - len(candidates)


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


def _first_error(validation: dict) -> str:
    errors = validation.get("errors") if isinstance(validation, dict) else None
    if isinstance(errors, list) and errors:
        issue = errors[0]
        if isinstance(issue, dict):
            return str(issue.get("message") or issue.get("code") or "客观事实提取失败。")[:500]
        return str(issue)[:500]
    return ""


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
    run.save(
        update_fields=[
            "request_count",
            "success_count",
            "failed_count",
            "skipped_count",
            "validation_passed_count",
            "validation_warning_count",
            "validation_error_count",
            "facts_count",
            "safe_error_summary",
            "updated_at",
        ]
    )


def _finish_run(run: ObjectiveFactExtractionRun) -> ObjectiveFactExtractionRun:
    if run.failed_count and run.success_count:
        run.status = ObjectiveFactExtractionRun.Status.PARTIAL
    elif run.failed_count:
        run.status = ObjectiveFactExtractionRun.Status.FAILED
    else:
        run.status = ObjectiveFactExtractionRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "safe_error_summary", "updated_at"])
    return run


def run_objective_fact_extraction(
    *,
    mode: str = ObjectiveFactExtractionRun.Mode.INCREMENTAL,
    trigger: str = ObjectiveFactExtractionRun.Trigger.COMMAND,
    record_ids: list[int] | None = None,
    prompt_version: str | None = None,
    client: DeepSeekObjectiveFactClient | None = None,
) -> ObjectiveFactExtractionRun:
    config = get_objective_fact_config(prompt_version=prompt_version)
    run = _create_run(trigger=trigger, mode=mode, config=config)
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
            result, article = _pending_result(
                run=run, record=record, config=config
            )
            run.request_count += 1
            try:
                processed = ai_client.process(article)
                _save_processed_result(result, processed, config)
            except Exception as exc:
                _save_unexpected_failure(result, exc)
            if result.extraction_status == ObjectiveFactExtractionResult.ExtractionStatus.SUCCESS:
                run.success_count += 1
                run.facts_count += result.facts_count
            else:
                run.failed_count += 1
                if result.safe_error_summary:
                    run.safe_error_summary = result.safe_error_summary
            if result.validation_status == ObjectiveFactExtractionResult.ValidationStatus.PASSED:
                run.validation_passed_count += 1
            elif result.validation_status == ObjectiveFactExtractionResult.ValidationStatus.WARNING:
                run.validation_warning_count += 1
            else:
                run.validation_error_count += 1
            _sync_run(run)
    finally:
        if owns_client:
            ai_client.close()
    return _finish_run(run)
