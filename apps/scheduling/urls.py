from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.schedule_index, name="index"),
    path("sources/", views.source_network_settings, name="sources"),
    path("runs/", views.schedule_runs, name="runs"),
    path(
        "runs/<str:run_kind>/<int:run_id>/",
        views.schedule_run_detail,
        name="run_detail",
    ),
]
