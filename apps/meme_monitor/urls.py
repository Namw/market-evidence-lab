from django.urls import path

from apps.meme_monitor import views

app_name = "meme_monitor"

urlpatterns = [
    path("", views.index, name="index"),
    path("anomalies/", views.anomalies, name="anomalies"),
    path("pairs/", views.pairs, name="pairs"),
    path("schedule/toggle/", views.toggle_schedule, name="toggle_schedule"),
]
