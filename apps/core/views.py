from django.db.models import Max
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.market_data.models import FundingRate, Kline, OpenInterest
from apps.news_analysis.models import (
    CanonicalEvent,
    EventMergeRun,
    ObjectiveFactExtractionResult,
)
from apps.news_data.models import NewsFeed, NewsRawRecord


@require_GET
def home(request):
    latest_kline_at = Kline.objects.aggregate(value=Max("open_time"))["value"]
    current_event_run = EventMergeRun.objects.filter(is_current_snapshot=True).first()

    context = {
        "market_record_count": (
            Kline.objects.count()
            + OpenInterest.objects.count()
            + FundingRate.objects.count()
        ),
        "latest_kline_at": latest_kline_at,
        "news_record_count": NewsRawRecord.objects.count(),
        "enabled_feed_count": NewsFeed.objects.filter(
            enabled=True, source__enabled=True
        ).count(),
        "eligible_fact_count": ObjectiveFactExtractionResult.objects.filter(
            facts_count__gt=0,
            validation_errors=[],
        ).count(),
        "event_count": (
            CanonicalEvent.objects.filter(run=current_event_run).count()
            if current_event_run
            else 0
        ),
    }
    return render(request, "core/home.html", context)
