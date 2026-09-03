"""URL configuration for Market Evidence Lab."""

from django.urls import include, path

urlpatterns = [
    path("trading-assistant/", include("apps.trading_assistant.urls")),
    path("meme-monitor/", include("apps.meme_monitor.urls")),
    path("microstructure/", include("apps.microstructure.urls")),
    path("market-data/", include("apps.market_data.urls")),
    path("market-funds/", include("apps.market_funds.urls")),
    path("analysis/news/", include("apps.news_analysis.urls")),
    path("system/schedules/", include("apps.scheduling.urls")),
    path("collection/news/", include("apps.news_data.urls")),
    path("", include("apps.core.urls")),
]
