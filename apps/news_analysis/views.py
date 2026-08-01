from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.news_data.models import NewsRawRecord, NewsSource

from .forms import NewsObservationFilterForm
from .models import NewsAnalysisResult, NewsAnalysisRun
from .services import AnalysisAlreadyRunning, run_news_analysis


@require_GET
def news_observations(request):
    version = settings.NEWS_AI_ANALYSIS_VERSION
    current_results = NewsAnalysisResult.objects.filter(analysis_version=version)
    raw_total = NewsRawRecord.objects.count()
    success_count = current_results.filter(
        status=NewsAnalysisResult.Status.SUCCESS
    ).count()
    failure_count = current_results.filter(
        status=NewsAnalysisResult.Status.FAILED
    ).count()
    observation_counts = {
        code: current_results.filter(
            status=NewsAnalysisResult.Status.SUCCESS, observation_result=code
        ).count()
        for code, _ in NewsAnalysisResult.ObservationResult.choices
    }
    method_counts = {
        code: current_results.filter(
            status=NewsAnalysisResult.Status.SUCCESS, method=code
        ).count()
        for code, _ in NewsAnalysisResult.Method.choices
    }
    source_distribution = list(
        NewsSource.objects.annotate(
            analyzed_count=Count(
                "raw_records__analysis_results",
                filter=Q(
                    raw_records__analysis_results__analysis_version=version,
                    raw_records__analysis_results__status=NewsAnalysisResult.Status.SUCCESS,
                ),
                distinct=True,
            ),
            noteworthy_count=Count(
                "raw_records__analysis_results",
                filter=Q(
                    raw_records__analysis_results__analysis_version=version,
                    raw_records__analysis_results__status=NewsAnalysisResult.Status.SUCCESS,
                    raw_records__analysis_results__observation_result=NewsAnalysisResult.ObservationResult.NOTEWORTHY,
                ),
                distinct=True,
            ),
            routine_count=Count(
                "raw_records__analysis_results",
                filter=Q(
                    raw_records__analysis_results__analysis_version=version,
                    raw_records__analysis_results__status=NewsAnalysisResult.Status.SUCCESS,
                    raw_records__analysis_results__observation_result=NewsAnalysisResult.ObservationResult.ROUTINE,
                ),
                distinct=True,
            ),
            noise_count=Count(
                "raw_records__analysis_results",
                filter=Q(
                    raw_records__analysis_results__analysis_version=version,
                    raw_records__analysis_results__status=NewsAnalysisResult.Status.SUCCESS,
                    raw_records__analysis_results__observation_result=NewsAnalysisResult.ObservationResult.NOISE,
                ),
                distinct=True,
            ),
            insufficient_count=Count(
                "raw_records__analysis_results",
                filter=Q(
                    raw_records__analysis_results__analysis_version=version,
                    raw_records__analysis_results__status=NewsAnalysisResult.Status.SUCCESS,
                    raw_records__analysis_results__observation_result=NewsAnalysisResult.ObservationResult.INSUFFICIENT,
                ),
                distinct=True,
            ),
        ).order_by("code")
    )

    form = NewsObservationFilterForm(request.GET or None)
    results = current_results.select_related("news_record__source", "analysis_run")
    if form.is_valid():
        filters = {
            field: value
            for field, value in form.cleaned_data.items()
            if field != "source" and value
        }
        if form.cleaned_data.get("source"):
            results = results.filter(news_record__source=form.cleaned_data["source"])
        results = results.filter(**filters)
    results = results.annotate(
        observation_priority=Case(
            When(
                observation_result=NewsAnalysisResult.ObservationResult.NOTEWORTHY,
                then=Value(0),
            ),
            When(
                observation_result=NewsAnalysisResult.ObservationResult.ROUTINE,
                then=Value(1),
            ),
            When(
                observation_result=NewsAnalysisResult.ObservationResult.INSUFFICIENT,
                then=Value(2),
            ),
            When(
                observation_result=NewsAnalysisResult.ObservationResult.NOISE,
                then=Value(3),
            ),
            default=Value(4),
            output_field=IntegerField(),
        ),
        importance_priority=Case(
            When(importance=NewsAnalysisResult.Level.HIGH, then=Value(0)),
            When(importance=NewsAnalysisResult.Level.MEDIUM, then=Value(1)),
            When(importance=NewsAnalysisResult.Level.LOW, then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        ),
    ).order_by(
        "observation_priority",
        "importance_priority",
        "-news_record__published_at",
        "-id",
    )
    page = Paginator(results, 100).get_page(request.GET.get("page"))
    active_run = NewsAnalysisRun.objects.filter(
        analysis_version=version, status=NewsAnalysisRun.Status.RUNNING
    ).first()
    return render(
        request,
        "news_analysis/index.html",
        {
            "analysis_version": version,
            "prompt_version": settings.NEWS_AI_PROMPT_VERSION,
            "model_name": settings.NEWS_AI_MODEL,
            "api_configured": bool(settings.NEWS_AI_API_KEY),
            "active_run": active_run,
            "raw_total": raw_total,
            "success_count": success_count,
            "failure_count": failure_count,
            "unanalyzed_count": max(raw_total - success_count - failure_count, 0),
            "observation_counts": observation_counts,
            "method_counts": method_counts,
            "source_distribution": source_distribution,
            "recent_runs": NewsAnalysisRun.objects.all()[:20],
            "filter_form": form,
            "page": page,
        },
    )


@require_POST
def run_analysis(request, mode: str):
    if mode not in {
        NewsAnalysisRun.Mode.INCREMENTAL,
        NewsAnalysisRun.Mode.RETRY_FAILED,
    }:
        messages.error(request, "无法识别的新闻分析运行模式。")
        return redirect("news_analysis:index")
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动新闻分析。")
        return redirect("news_analysis:index")
    try:
        run = run_news_analysis(mode=mode, trigger=NewsAnalysisRun.Trigger.MANUAL)
    except AnalysisAlreadyRunning:
        messages.warning(request, "当前分析版本已有运行中的任务，请勿重复启动。")
    except Exception:
        messages.error(request, "新闻分析运行失败，请查看安全错误摘要。")
    else:
        if run.status == NewsAnalysisRun.Status.SUCCESS:
            messages.success(request, "新闻分析运行完成。")
        elif run.status == NewsAnalysisRun.Status.PARTIAL:
            messages.warning(request, "新闻分析部分完成，请查看失败项与运行记录。")
        else:
            messages.error(request, "新闻分析未成功完成，请查看安全错误摘要。")
    return redirect("news_analysis:index")
