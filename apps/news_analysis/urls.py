from django.urls import path

from . import views

app_name = "news_analysis"

urlpatterns = [
    path("", views.news_observations, name="index"),
    path("events/", views.event_overview, name="event_overview"),
    path("events/runs/", views.event_run_list, name="event_run_list"),
    path("events/runs/<int:run_id>/", views.event_run_detail, name="event_run_detail"),
    path("events/results/", views.event_list, name="event_list"),
    path("events/results/<int:event_id>/", views.event_detail, name="event_detail"),
    path("events/rebuild/", views.event_rebuild, name="event_rebuild"),
    path(
        "events/runs/<int:run_id>/retry-failed/",
        views.event_retry_failed,
        name="event_retry_failed",
    ),
    path(
        "events/runs/<int:run_id>/decisions/<int:decision_id>/retry/",
        views.event_retry_pair,
        name="event_retry_pair",
    ),
    path("objective-facts/", views.objective_fact_list, name="objective_fact_list"),
    path(
        "objective-facts/<int:news_id>/",
        views.objective_fact_detail,
        name="objective_fact_detail",
    ),
    path(
        "objective-facts/<int:news_id>/run/<str:mode>/",
        views.objective_fact_single_run,
        name="objective_fact_single_run",
    ),
    path(
        "objective-facts/run/<str:mode>/confirm/",
        views.objective_fact_run_confirm,
        name="objective_fact_run_confirm",
    ),
    path(
        "objective-facts/run/<str:mode>/",
        views.objective_fact_run,
        name="objective_fact_run",
    ),
    path(
        "objective-facts/runs/<int:run_id>/",
        views.objective_fact_run_detail,
        name="objective_fact_run_detail",
    ),
    path("results/<int:result_id>/content/", views.result_content, name="result_content"),
    path("run/<str:mode>/", views.run_analysis, name="run"),
]
