"""URL configuration for Market Evidence Lab."""

from django.urls import include, path

urlpatterns = [
    path("", include("apps.core.urls")),
]
