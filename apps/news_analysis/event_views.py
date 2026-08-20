import secrets

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .event_merge import (
    EventMergeAlreadyRunning,
    retry_failed_event_pairs,
    run_event_merge,
)
from .forms import CanonicalEventFilterForm, EventMergeRunFilterForm
from .models import CanonicalEvent, EventMergeRun, EventPairDecision

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

