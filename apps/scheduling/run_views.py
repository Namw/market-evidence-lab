from datetime import timedelta
from itertools import chain

from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from .models import (
    NewsAIWorkflowRun,
    NewsWorkflowRun,
    SCHEDULE_TIMEZONE,
    WorkflowRun,
)
from .view_presentation import (
    run_date_range,
    run_list_item,
    step_runs,
    workflow_summary,
    news_ai_workflow_summary,
    news_workflow_summary,
)

@require_http_methods(["GET"])
def schedule_runs(request):
    selected_task = request.GET.get("task", "all")
    if selected_task not in {"all", "market", "news", "news_ai"}:
        selected_task = "all"

    run_range = run_date_range(request)
    date_filters = {
        "started_at__gte": run_range["start_at"],
        "started_at__lt": run_range["end_at"],
    }
    market_queryset = WorkflowRun.objects.filter(**date_filters).select_related(
        "schedule"
    )
    news_queryset = NewsWorkflowRun.objects.filter(**date_filters).select_related(
        "schedule"
    )
    news_ai_queryset = NewsAIWorkflowRun.objects.filter(**date_filters).select_related(
        "schedule"
    )
    market_count = market_queryset.count()
    news_count = news_queryset.count()
    news_ai_count = news_ai_queryset.count()
    market_runs = list(market_queryset[:100])
    news_runs = list(news_queryset[:100])
    news_ai_runs = list(news_ai_queryset[:100])
    all_items = sorted(
        chain(
            (run_list_item(run, kind="market") for run in market_runs),
            (run_list_item(run, kind="news") for run in news_runs),
            (run_list_item(run, kind="news_ai") for run in news_ai_runs),
        ),
        key=lambda item: (item["started_at"], item["id"]),
        reverse=True,
    )
    if selected_task != "all":
        all_items = [item for item in all_items if item["kind"] == selected_task]

    return render(
        request,
        "scheduling/runs.html",
        {
            "run_items": all_items[:100],
            "selected_task": selected_task,
            "market_count": market_count,
            "news_count": news_count,
            "news_ai_count": news_ai_count,
            "attention_count": sum(item["needs_attention"] for item in all_items),
            "filter_start": run_range["start_date"].isoformat(),
            "filter_end": run_range["end_date"].isoformat(),
            "date_range_label": run_range["label"],
            "filter_error": run_range["error"],
            "display_timezone": SCHEDULE_TIMEZONE,
            "recent_three_start": (
                run_range["today"] - timedelta(days=2)
            ).isoformat(),
            "recent_three_end": run_range["today"].isoformat(),
        },
    )


@require_http_methods(["GET"])
def schedule_run_detail(request, run_kind: str, run_id: int):
    if run_kind == "market":
        run = get_object_or_404(
            WorkflowRun.objects.select_related("schedule"),
            pk=run_id,
        )
        context = {
            "run_kind": run_kind,
            "run": run,
            "summary": workflow_summary(run),
            "selected_steps": step_runs(run),
        }
    elif run_kind == "news":
        run = get_object_or_404(
            NewsWorkflowRun.objects.select_related(
                "schedule",
                "ethereum_collection_run",
                "binance_collection_run",
                "ethereum_inspection_run",
                "binance_inspection_run",
                "analysis_run",
            ).prefetch_related(
                "feed_steps__feed__source",
                "feed_steps__collection_run",
                "feed_steps__inspection_run",
            ),
            pk=run_id,
        )
        context = {
            "run_kind": run_kind,
            "run": run,
            "summary": _newsworkflow_summary(run),
            "news_feed_steps": run.feed_steps.all(),
        }
    elif run_kind == "news_ai":
        run = get_object_or_404(
            NewsAIWorkflowRun.objects.select_related(
                "schedule",
                "analysis_run",
                "objective_fact_run",
                "event_merge_run",
            ),
            pk=run_id,
        )
        context = {
            "run_kind": run_kind,
            "run": run,
            "summary": _news_aiworkflow_summary(run),
        }
    else:
        raise Http404("未知的调度任务类型")
    return render(request, "scheduling/run_detail.html", context)

