from urllib.parse import urlsplit

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.news_data.models import NewsRawRecord

from .fact_validation import database_article_input
from .forms import ObjectiveFactFilterForm
from .models import ObjectiveFactExtractionResult, ObjectiveFactExtractionRun
from .objective_fact_presentation import highlighted_segments
from .objective_facts import (
    get_objective_fact_config,
    objective_fact_single_mode,
)

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



