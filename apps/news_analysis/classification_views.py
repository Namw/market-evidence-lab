from datetime import timedelta

from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .forms import NewsClassificationFilterForm
from .models import NewsAnalysisResult, NewsAnalysisRun
from .services import prune_expired_news

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



