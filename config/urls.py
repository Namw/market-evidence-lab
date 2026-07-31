"""URL configuration for Market Evidence Lab."""

from django.urls import include, path

urlpatterns = [
    path("collection/", include("apps.collection.urls")),
    path("", include("apps.core.urls")),
]
