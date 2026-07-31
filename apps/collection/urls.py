from django.urls import path

from . import views

app_name = "collection"

urlpatterns = [
    path("", views.collection_index, name="index"),
    path("derivatives/", views.derivatives_collection, name="derivatives"),
]
