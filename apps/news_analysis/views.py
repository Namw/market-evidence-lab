from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .content import SourceContentError, fetch_source_article, summarize_article_text
from .forms import NewsClassificationFilterForm
from .models import NewsAnalysisResult, NewsAnalysisRun
from .services import AnalysisAlreadyRunning, prune_expired_news, run_news_analysis
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
