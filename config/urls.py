"""URL configuration for Market Evidence Lab."""

from django.urls import include, path

urlpatterns = [
    path("market-data/", include("apps.market_data.urls")),
    path("analysis/news/", include("apps.news_analysis.urls")),
    path("system/schedules/", include("apps.scheduling.urls")),
    path("collection/news/", include("apps.news_data.urls")),
    path("", include("apps.core.urls")),
]
