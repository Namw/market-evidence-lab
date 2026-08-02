from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.schedule_index, name="index"),
    path("runs/", views.schedule_runs, name="runs"),
    path(
        "runs/<str:run_kind>/<int:run_id>/",
        views.schedule_run_detail,
        name="run_detail",
    ),
]
