from django.urls import path

from . import views

app_name = "news_data"

urlpatterns = [
    path("", views.news_collection, name="index"),
    path("feeds/<slug:feed_code>/run/", views.run_news_collection, name="run"),
]
