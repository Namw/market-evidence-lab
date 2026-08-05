from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .content import SourceContentError, fetch_source_article, summarize_article_text
from .fact_validation import database_article_input
from .event_merge import (
    EventMergeAlreadyRunning,
    retry_failed_event_pairs,
    run_event_merge,
)
from .forms import (
    CanonicalEventFilterForm,
    EventMergeRunFilterForm,
    NewsClassificationFilterForm,
    ObjectiveFactFilterForm,
)
from .models import (
    CanonicalEvent,
    EventMembership,
    EventMergeRun,
    EventPairDecision,
    NewsAnalysisResult,
    NewsAnalysisRun,
    ObjectiveFactExtractionResult,
    ObjectiveFactExtractionRun,
)
from .objective_fact_presentation import highlighted_segments
from .objective_facts import (
    BATCH_MODES,
    SINGLE_MODES,
    ObjectiveFactAlreadyRunning,
    get_objective_fact_config,
    objective_fact_selection_count,
    objective_fact_single_mode,
    run_objective_fact_extraction,
)
from .services import AnalysisAlreadyRunning, prune_expired_news, run_news_analysis
from apps.news_data.models import NewsRawRecord
from apps.news_data.sources import SUMMARY_ONLY_SOURCE_CODES


@require_GET
def news_observations(request):
    prune_expired_news()
    version = settings.NEWS_AI_ANALYSIS_VERSION
    recent_since = timezone.now() - timedelta(days=3)
    form = NewsClassificationFilterForm(request.GET or None)
    results = (
        NewsAnalysisResult.objects.filter(
            analysis_version=version,
            status=NewsAnalysisResult.Status.SUCCESS,
        )
        .exclude(conclusion=NewsAnalysisResult.Conclusion.IRRELEVANT)
        .select_related("news_record__source", "analysis_run")
        .prefetch_related("news_record__feeds")
    )
    range_label = "最近 3 天"
    if form.is_valid():
        start_time = form.cleaned_data.get("start_time")
        end_time = form.cleaned_data.get("end_time")
        if start_time or end_time:
            range_label = "自定义分类时间"
            if start_time:
                results = results.filter(analyzed_at__gte=start_time)
            if end_time:
                results = results.filter(analyzed_at__lte=end_time)
        else:
            results = results.filter(analyzed_at__gte=recent_since)
        if form.cleaned_data.get("source"):
            results = results.filter(news_record__source=form.cleaned_data["source"])
        if form.cleaned_data.get("authority_level"):
            results = results.filter(
                news_record__source__authority_level=form.cleaned_data[
                    "authority_level"
                ]
            )
        if form.cleaned_data.get("conclusion"):
            results = results.filter(conclusion=form.cleaned_data["conclusion"])
        if form.cleaned_data.get("classification_stage"):
            results = results.filter(
                classification_stage=form.cleaned_data["classification_stage"]
            )
    else:
        results = results.filter(analyzed_at__gte=recent_since)
    page = Paginator(results.order_by("-analyzed_at", "-id"), 100).get_page(
        request.GET.get("page")
    )
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    return render(
        request,
        "news_analysis/index.html",
        {
            "analysis_version": version,
            "api_configured": bool(settings.NEWS_AI_API_KEY),
            "active_run": NewsAnalysisRun.objects.filter(
                analysis_version=version, status=NewsAnalysisRun.Status.RUNNING
            ).first(),
            "filter_form": form,
            "page": page,
            "range_label": range_label,
            "pagination_query": pagination_query.urlencode(),
        },
    )


@require_GET
def result_content(request, result_id: int):
    result = get_object_or_404(
        NewsAnalysisResult.objects.select_related("news_record__source"),
        pk=result_id,
        status=NewsAnalysisResult.Status.SUCCESS,
    )
    record = result.news_record
    source_url = record.original_url or record.canonical_url
    if record.source.code in SUMMARY_ONLY_SOURCE_CODES:
        return JsonResponse(
            {
                "origin": "saved_summary",
                "content": result.content_summary
                or record.summary
                or "来源暂未提供可显示的摘要。",
                "source_url": source_url,
            }
        )
    try:
        article = fetch_source_article(record)
    except SourceContentError:
        fallback = result.content_summary or record.summary
        return JsonResponse(
            {
                "origin": "saved_summary",
                "content": fallback or "暂未采集到可显示的正文摘要。",
                "source_url": source_url,
            }
        )

    if not result.content_summary:
        result.content_summary = summarize_article_text(article.text)
        result.save(update_fields=["content_summary", "updated_at"])
    return JsonResponse(
        {
            "origin": "source",
            "content": article.text,
            "source_url": article.source_url or source_url,
        }
    )


@require_POST
def run_analysis(request, mode: str):
    if mode not in {
        NewsAnalysisRun.Mode.INCREMENTAL,
        NewsAnalysisRun.Mode.RETRY_FAILED,
    }:
        messages.error(request, "无法识别的新闻分类运行模式。")
        return redirect("news_analysis:index")
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动新闻分类。")
        return redirect("news_analysis:index")
    try:
        run = run_news_analysis(mode=mode, trigger=NewsAnalysisRun.Trigger.MANUAL)
    except AnalysisAlreadyRunning:
        messages.warning(request, "当前分类版本已有运行中的任务，请勿重复启动。")
    except Exception:
        messages.error(request, "新闻分类运行失败，请检查运行日志。")
    else:
        if run.status == NewsAnalysisRun.Status.SUCCESS:
            messages.success(request, "ETH 新闻分类完成。")
        elif run.status == NewsAnalysisRun.Status.PARTIAL:
            messages.warning(request, "ETH 新闻分类部分完成，失败项会在下次重试。")
        else:
            messages.error(request, "ETH 新闻分类未成功完成。")
    return redirect("news_analysis:index")


def _latest_objective_fact_subquery(prompt_version: str):
    return ObjectiveFactExtractionResult.objects.filter(
        news_record_id=OuterRef("pk"), prompt_version=prompt_version
    ).order_by("-extracted_at", "-id")


@require_GET
def objective_fact_list(request):
    config = get_objective_fact_config()
    latest = _latest_objective_fact_subquery(config.prompt_version)
    records = NewsRawRecord.objects.select_related("source").annotate(
        latest_result_id=Subquery(latest.values("id")[:1]),
        latest_objective_summary=Subquery(latest.values("objective_summary")[:1]),
        latest_event_status=Subquery(latest.values("event_status")[:1]),
        latest_information_completeness=Subquery(
            latest.values("information_completeness")[:1]
        ),
        latest_extraction_status=Subquery(latest.values("extraction_status")[:1]),
        latest_validation_status=Subquery(latest.values("validation_status")[:1]),
        latest_facts_count=Subquery(latest.values("facts_count")[:1]),
        latest_extracted_at=Subquery(latest.values("extracted_at")[:1]),
    )
    form = ObjectiveFactFilterForm(request.GET or None)
    if form.is_valid():
        values = form.cleaned_data
        if values.get("published_start"):
            records = records.filter(
                published_at__date__gte=values["published_start"]
            )
        if values.get("published_end"):
            records = records.filter(published_at__date__lte=values["published_end"])
        if values.get("source"):
            records = records.filter(source=values["source"])
        if values.get("keyword"):
            keyword = values["keyword"]
            records = records.filter(
                Q(title__icontains=keyword)
                | Q(latest_objective_summary__icontains=keyword)
            )
        if values.get("event_status"):
            records = records.filter(latest_event_status=values["event_status"])
        if values.get("information_completeness"):
            records = records.filter(
                latest_information_completeness=values["information_completeness"]
            )
        extraction_status = values.get("extraction_status")
        if extraction_status == "not_extracted":
            records = records.filter(latest_result_id__isnull=True)
        elif extraction_status:
            records = records.filter(latest_extraction_status=extraction_status)
        if values.get("validation_status"):
            records = records.filter(
                latest_validation_status=values["validation_status"]
            )
        facts_count = values.get("facts_count")
        if facts_count == "zero":
            records = records.filter(
                latest_result_id__isnull=False, latest_facts_count=0
            )
        elif facts_count == "one":
            records = records.filter(latest_facts_count=1)
        elif facts_count == "multiple":
            records = records.filter(latest_facts_count__gte=2)
        has_body = values.get("has_body")
        if has_body:
            all_ids = set(NewsRawRecord.objects.values_list("id", flat=True))
            body_ids = {
                record.id
                for record in NewsRawRecord.objects.only("id", "summary", "raw_payload")
                if bool(database_article_input(record).stored_body)
            }
            records = records.filter(
                id__in=body_ids if has_body == "yes" else all_ids - body_ids
            )
    page = Paginator(records.order_by("-published_at", "-id"), 50).get_page(
        request.GET.get("page")
    )
    result_ids = [
        record.latest_result_id
        for record in page.object_list
        if record.latest_result_id is not None
    ]
    results = {
        result.id: result
        for result in ObjectiveFactExtractionResult.objects.filter(id__in=result_ids)
    }
    for record in page.object_list:
        record.objective_fact_result = results.get(record.latest_result_id)
        record.objective_fact_action = objective_fact_single_mode(
            record.objective_fact_result
        )
        record.objective_fact_action_label = {
            ObjectiveFactExtractionRun.Mode.SINGLE: "提取",
            ObjectiveFactExtractionRun.Mode.RETRY_SINGLE: "重试",
            ObjectiveFactExtractionRun.Mode.REEXTRACT: "重新提取",
        }[record.objective_fact_action]
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    current_results = ObjectiveFactExtractionResult.objects.filter(
        prompt_version=config.prompt_version
    )
    return render(
        request,
        "news_analysis/objective_fact_list.html",
        {
            "page": page,
            "filter_form": form,
            "prompt_version": config.prompt_version,
            "pagination_query": pagination_query.urlencode(),
            "api_configured": bool(settings.NEWS_AI_API_KEY),
            "active_run": ObjectiveFactExtractionRun.objects.filter(
                status=ObjectiveFactExtractionRun.Status.RUNNING
            ).first(),
            "recent_runs": ObjectiveFactExtractionRun.objects.all()[:5],
            "current_result_count": current_results.count(),
            "current_news_count": current_results.values("news_record_id")
            .distinct()
            .count(),
            "total_news_count": NewsRawRecord.objects.count(),
            "historical_result_count": ObjectiveFactExtractionResult.objects.exclude(
                prompt_version=config.prompt_version
            ).count(),
        },
    )


def _objective_fact_operator(request) -> str:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        username = user.get_username()
        return username or f"user:{user.pk}"
    return "anonymous"


def _batch_mode(mode: str) -> str | None:
    return mode if mode in BATCH_MODES else None


@require_GET
def objective_fact_run_confirm(request, mode: str):
    mode = _batch_mode(mode)
    if mode is None:
        messages.error(request, "无法识别的客观事实批量运行模式。")
        return redirect("news_analysis:objective_fact_list")
    config = get_objective_fact_config()
    candidate_count, skipped_count = objective_fact_selection_count(mode=mode)
    scope_text = {
        ObjectiveFactExtractionRun.Mode.INCREMENTAL: (
            "处理当前版本下没有任何成功提取结果的新闻；旧版本成功结果不会导致跳过。"
        ),
        ObjectiveFactExtractionRun.Mode.RETRY_FAILED: (
            "仅处理当前版本最新结果为模型/解析失败、校验 error 或其他无效执行的新闻；可用 warning 不重试。"
        ),
    }[mode]
    return render(
        request,
        "news_analysis/objective_fact_confirm.html",
        {
            "mode": mode,
            "mode_label": dict(ObjectiveFactExtractionRun.Mode.choices)[mode],
            "prompt_version": config.prompt_version,
            "model": config.model,
            "candidate_count": candidate_count,
            "skipped_count": skipped_count,
            "scope_text": scope_text,
            "api_configured": bool(settings.NEWS_AI_API_KEY),
            "active_run": ObjectiveFactExtractionRun.objects.filter(
                status=ObjectiveFactExtractionRun.Status.RUNNING
            ).first(),
        },
    )


@require_POST
def objective_fact_run(request, mode: str):
    mode = _batch_mode(mode)
    if mode is None or request.POST.get("confirm") != "yes":
        messages.error(request, "客观事实提取确认参数无效。")
        return redirect("news_analysis:objective_fact_list")
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动客观事实提取。")
        return redirect("news_analysis:objective_fact_run_confirm", mode=mode)
    try:
        run = run_objective_fact_extraction(
            mode=mode,
            trigger=ObjectiveFactExtractionRun.Trigger.MANUAL,
            triggered_by=_objective_fact_operator(request),
        )
    except ObjectiveFactAlreadyRunning:
        messages.warning(request, "当前已有客观事实提取任务正在运行，请勿重复启动。")
        return redirect("news_analysis:objective_fact_list")
    except Exception:
        messages.error(request, "客观事实提取未能启动，请检查运行记录和服务日志。")
        return redirect("news_analysis:objective_fact_list")
    return redirect("news_analysis:objective_fact_run_detail", run_id=run.id)


@require_POST
def objective_fact_single_run(request, news_id: int, mode: str):
    record = get_object_or_404(NewsRawRecord, pk=news_id)
    if mode not in SINGLE_MODES:
        messages.error(request, "无法识别的单条客观事实提取模式。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    try:
        objective_fact_selection_count(mode=mode, record_ids=[record.id])
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动客观事实提取。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    try:
        run = run_objective_fact_extraction(
            mode=mode,
            trigger=ObjectiveFactExtractionRun.Trigger.MANUAL,
            triggered_by=_objective_fact_operator(request),
            record_ids=[record.id],
        )
    except ObjectiveFactAlreadyRunning:
        messages.warning(request, "当前已有客观事实提取任务正在运行，请稍后重试。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    except Exception:
        messages.error(request, "单条客观事实提取未能启动，请检查运行记录。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    return redirect("news_analysis:objective_fact_run_detail", run_id=run.id)


@require_GET
def objective_fact_run_detail(request, run_id: int):
    run = get_object_or_404(ObjectiveFactExtractionRun, pk=run_id)
    results = run.results.select_related("news_record__source").order_by(
        "created_at", "id"
    )
    return render(
        request,
        "news_analysis/objective_fact_run_detail.html",
        {"run": run, "results": results},
    )


def _issues_for_fact(issues: object, index: int) -> list[dict]:
    if not isinstance(issues, list):
        return []
    prefix = f"facts[{index}]"
    return [
        issue
        for issue in issues
        if isinstance(issue, dict)
        and str(issue.get("path", "")).startswith(prefix)
    ]


def _safe_source_url(record: NewsRawRecord) -> str:
    candidate = record.original_url or record.canonical_url
    parsed = urlsplit(candidate)
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


@require_GET
def objective_fact_detail(request, news_id: int):
    record = get_object_or_404(
        NewsRawRecord.objects.select_related("source"), pk=news_id
    )
    config = get_objective_fact_config()
    history = list(
        ObjectiveFactExtractionResult.objects.filter(
            news_record=record
        )
        .select_related("extraction_run")
        .order_by("-extracted_at", "-id")
    )
    current_result = next(
        (
            item
            for item in history
            if item.prompt_version == config.prompt_version
        ),
        None,
    )
    selected_result_id = request.GET.get("result")
    if selected_result_id:
        try:
            selected_id = int(selected_result_id)
        except (TypeError, ValueError):
            selected_id = -1
        result = next((item for item in history if item.id == selected_id), None)
        if result is None:
            result = get_object_or_404(
                ObjectiveFactExtractionResult,
                pk=selected_id,
                news_record=record,
            )
    else:
        result = current_result
    action_mode = objective_fact_single_mode(current_result)
    action_label = {
        ObjectiveFactExtractionRun.Mode.SINGLE: "提取",
        ObjectiveFactExtractionRun.Mode.RETRY_SINGLE: "重试",
        ObjectiveFactExtractionRun.Mode.REEXTRACT: "重新提取",
    }[action_mode]
    current_input = database_article_input(record)
    input_snapshot = (
        result.input_snapshot
        if result and isinstance(result.input_snapshot, dict)
        else {
            "title": current_input.title,
            "summary": current_input.summary,
            "stored_body": current_input.stored_body,
            "published_at": current_input.published_at,
        }
    )
    parsed = (
        result.parsed_result
        if result and isinstance(result.parsed_result, dict)
        else {}
    )
    facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
    prepared_facts = []
    evidences_by_field = {"title": [], "summary": [], "stored_body": []}
    matches = (
        result.evidence_matches
        if result and isinstance(result.evidence_matches, list)
        else []
    )
    for index, raw_fact in enumerate(facts):
        fact = (
            raw_fact
            if isinstance(raw_fact, dict)
            else {"statement": str(raw_fact)}
        )
        match = next(
            (
                item
                for item in matches
                if isinstance(item, dict) and item.get("fact_index") == index
            ),
            {"matched": False, "match_type": "unmatched", "matched_field": None},
        )
        evidence = fact.get("evidence_text")
        matched_field = match.get("matched_field")
        if (
            match.get("matched")
            and matched_field in evidences_by_field
            and isinstance(evidence, str)
        ):
            evidences_by_field[matched_field].append(evidence)
        prepared_facts.append(
            {
                "data": fact,
                "match": match,
                "errors": _issues_for_fact(result.validation_errors, index)
                if result
                else [],
                "warnings": _issues_for_fact(result.validation_warnings, index)
                if result
                else [],
            }
        )
    input_sections = []
    for field, label in (
        ("title", "标题"),
        ("summary", "摘要"),
        ("stored_body", "正文"),
    ):
        value = input_snapshot.get(field)
        value = value if isinstance(value, str) else ""
        input_sections.append(
            {
                "field": field,
                "label": label,
                "value": value,
                "segments": highlighted_segments(value, evidences_by_field[field]),
            }
        )
    return render(
        request,
        "news_analysis/objective_fact_detail.html",
        {
            "record": record,
            "result": result,
            "parsed": parsed,
            "facts": prepared_facts,
            "input_snapshot": input_snapshot,
            "input_sections": input_sections,
            "saved_body": current_input.stored_body,
            "source_url": _safe_source_url(record),
            "prompt_version": config.prompt_version,
            "current_result": current_result,
            "history": history,
            "action_mode": action_mode,
            "action_label": action_label,
            "api_configured": bool(settings.NEWS_AI_API_KEY),
            "active_run": ObjectiveFactExtractionRun.objects.filter(
                status=ObjectiveFactExtractionRun.Status.RUNNING
            ).first(),
        },
    )


def _event_request_key() -> str:
    return secrets.token_hex(24)


@require_GET
def event_overview(request):
    current = EventMergeRun.objects.filter(is_current_snapshot=True).first()
    latest = EventMergeRun.objects.first()
    active = EventMergeRun.objects.filter(status=EventMergeRun.Status.RUNNING).first()
    return render(
        request,
        "news_analysis/event_overview.html",
        {
            "current_run": current,
            "latest_run": latest,
            "active_run": active,
            "recent_runs": EventMergeRun.objects.all()[:6],
            "retryable_count": (
                latest.pair_decisions.filter(
                    relation=EventPairDecision.Relation.PROCESSING_FAILED,
                    is_retryable=True,
                ).count()
                if latest
                else 0
            ),
            "request_key": _event_request_key(),
        },
    )


@require_GET
def event_run_list(request):
    runs = EventMergeRun.objects.select_related("original_run")
    form = EventMergeRunFilterForm(request.GET or None)
    if form.is_valid():
        values = form.cleaned_data
        if values.get("status"):
            runs = runs.filter(status=values["status"])
        if values.get("trigger"):
            runs = runs.filter(trigger=values["trigger"])
        if values.get("started_from"):
            runs = runs.filter(started_at__date__gte=values["started_from"])
        if values.get("started_to"):
            runs = runs.filter(started_at__date__lte=values["started_to"])
        for field in ("algorithm_version", "prompt_version", "model"):
            if values.get(field):
                runs = runs.filter(**{field: values[field]})
    page = Paginator(runs, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "news_analysis/event_run_list.html",
        {"page": page, "filter_form": form},
    )


@require_GET
def event_run_detail(request, run_id: int):
    run = get_object_or_404(
        EventMergeRun.objects.select_related(
            "original_run", "retry_pair_decision"
        ),
        pk=run_id,
    )
    decisions = run.pair_decisions.select_related(
        "left_result__news_record", "right_result__news_record"
    )
    relation_counts = {
        relation: decisions.filter(relation=relation).count()
        for relation, _ in EventPairDecision.Relation.choices
    }
    return render(
        request,
        "news_analysis/event_run_detail.html",
        {
            "run": run,
            "relation_counts": relation_counts,
            "failed_decisions": decisions.filter(
                relation=EventPairDecision.Relation.PROCESSING_FAILED
            ),
            "retryable_count": decisions.filter(
                relation=EventPairDecision.Relation.PROCESSING_FAILED,
                is_retryable=True,
            ).count(),
            "stages": EventMergeRun.Stage.choices[1:-1],
            "request_key": _event_request_key(),
        },
    )


@require_GET
def event_list(request):
    requested_run = request.GET.get("run")
    if requested_run:
        run = get_object_or_404(EventMergeRun, pk=requested_run)
    else:
        run = EventMergeRun.objects.filter(is_current_snapshot=True).first()
    events = CanonicalEvent.objects.none()
    if run is not None:
        events = run.events.all()
    form = CanonicalEventFilterForm(request.GET or None)
    if form.is_valid():
        values = form.cleaned_data
        if values.get("status"):
            events = events.filter(status=values["status"])
        if values.get("grouping_method"):
            events = events.filter(grouping_method=values["grouping_method"])
        if values.get("source"):
            events = events.filter(
                memberships__news_record__source=values["source"]
            ).distinct()
        if values.get("publication_start"):
            events = events.filter(
                latest_publication_at__date__gte=values["publication_start"]
            )
        if values.get("publication_end"):
            events = events.filter(
                earliest_publication_at__date__lte=values["publication_end"]
            )
        if values.get("keyword"):
            keyword = values["keyword"]
            events = events.filter(
                Q(canonical_title__icontains=keyword)
                | Q(objective_summary__icontains=keyword)
                | Q(action_snapshot__icontains=keyword)
            )
        if values.get("min_members"):
            events = events.filter(member_count__gte=values["min_members"])
    page = Paginator(events, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "news_analysis/event_list.html",
        {"run": run, "page": page, "filter_form": form},
    )


@require_GET
def event_detail(request, event_id: int):
    event = get_object_or_404(
        CanonicalEvent.objects.select_related("run"), pk=event_id
    )
    memberships = event.memberships.select_related(
        "extraction_result", "news_record__source"
    ).order_by("news_record__published_at", "id")
    result_ids = list(memberships.values_list("extraction_result_id", flat=True))
    decisions = event.run.pair_decisions.filter(
        Q(left_result_id__in=result_ids) | Q(right_result_id__in=result_ids)
    ).select_related("left_result__news_record", "right_result__news_record")
    return render(
        request,
        "news_analysis/event_detail.html",
        {"event": event, "memberships": memberships, "decisions": decisions},
    )


@require_POST
def event_rebuild(request):
    try:
        run = run_event_merge(
            trigger=EventMergeRun.Trigger.FULL_REBUILD,
            request_key=request.POST.get("request_key") or None,
        )
    except EventMergeAlreadyRunning as exc:
        messages.warning(request, str(exc))
        return redirect("news_analysis:event_overview")
    except Exception:
        messages.error(request, "事件库构建发生内部错误；旧有效结果未被覆盖。")
        return redirect("news_analysis:event_overview")
    if run.status == EventMergeRun.Status.SUCCEEDED:
        messages.success(request, "暂定新闻事件库已重建并切换为当前有效快照。")
    elif run.status == EventMergeRun.Status.SUCCEEDED_WITH_WARNINGS:
        messages.warning(request, "事件库已保守完成；部分 AI 比较失败并保持独立。")
    else:
        messages.error(request, "事件库构建失败；旧有效结果继续可用。")
    return redirect("news_analysis:event_run_detail", run_id=run.id)


@require_POST
def event_retry_failed(request, run_id: int):
    original = get_object_or_404(EventMergeRun, pk=run_id)
    try:
        run = retry_failed_event_pairs(
            original,
            request_key=request.POST.get("request_key") or None,
        )
    except (ValueError, EventMergeAlreadyRunning) as exc:
        messages.warning(request, str(exc))
        return redirect("news_analysis:event_run_detail", run_id=original.id)
    except Exception:
        messages.error(request, "失败项重试发生内部错误；旧有效结果未被覆盖。")
        return redirect("news_analysis:event_run_detail", run_id=original.id)
    return redirect("news_analysis:event_run_detail", run_id=run.id)


@require_POST
def event_retry_pair(request, run_id: int, decision_id: int):
    original = get_object_or_404(EventMergeRun, pk=run_id)
    decision = get_object_or_404(EventPairDecision, pk=decision_id, run=original)
    try:
        run = retry_failed_event_pairs(
            original,
            pair_decision=decision,
            request_key=request.POST.get("request_key") or None,
        )
    except (ValueError, EventMergeAlreadyRunning) as exc:
        messages.warning(request, str(exc))
        return redirect("news_analysis:event_run_detail", run_id=original.id)
    except Exception:
        messages.error(request, "单项重试发生内部错误；旧有效结果未被覆盖。")
        return redirect("news_analysis:event_run_detail", run_id=original.id)
    return redirect("news_analysis:event_run_detail", run_id=run.id)
