"""URL configuration for Market Evidence Lab."""

from django.urls import include, path

urlpatterns = [
    path("research-cases/", include("apps.research_cases.urls")),
    path("market-inspection/", include("apps.market_monitoring.urls")),
    path("system/schedules/", include("apps.scheduling.urls")),
    path("collection/", include("apps.collection.urls")),
    path("inspection/", include("apps.inspection.urls")),
    path("", include("apps.core.urls")),
]
