from django.urls import path

from . import views

app_name = "news_analysis"

urlpatterns = [
    path("", views.news_observations, name="index"),
    path("results/<int:result_id>/content/", views.result_content, name="result_content"),
    path("run/<str:mode>/", views.run_analysis, name="run"),
]
