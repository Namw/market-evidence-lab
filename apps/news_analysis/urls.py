from django.urls import path

from . import views

app_name = "news_analysis"

urlpatterns = [
    path("", views.news_observations, name="index"),
    path("objective-facts/", views.objective_fact_list, name="objective_fact_list"),
    path(
        "objective-facts/<int:news_id>/",
        views.objective_fact_detail,
        name="objective_fact_detail",
    ),
    path("results/<int:result_id>/content/", views.result_content, name="result_content"),
    path("run/<str:mode>/", views.run_analysis, name="run"),
]
