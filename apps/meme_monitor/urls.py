from django.urls import path

from apps.meme_monitor import views

app_name = "meme_monitor"

urlpatterns = [
    path("", views.index, name="index"),
]
