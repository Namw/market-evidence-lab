"""URL configuration for Market Evidence Lab."""

from django.urls import include, path

urlpatterns = [
    path("market-data/", include("apps.market_data.urls")),
    path("analysis/news/", include("apps.news_analysis.urls")),
    path("research-cases/", include("apps.derivatives_evidence.urls")),
    path("research-cases/", include("apps.price_evidence.urls")),
    path("research-cases/", include("apps.research_cases.urls")),
    path("market-inspection/", include("apps.market_monitoring.urls")),
    path("system/schedules/", include("apps.scheduling.urls")),
    path("collection/", include("apps.collection.urls")),
    path("collection/news/", include("apps.news_data.urls")),
    path("inspection/", include("apps.inspection.urls")),
    path("", include("apps.core.urls")),
]
