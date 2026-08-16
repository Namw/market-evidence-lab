from django.urls import path

from . import views

app_name = "microstructure"

urlpatterns = [
    path("", views.index, name="index"),
    path("status/", views.status, name="status"),
    path("start/", views.start, name="start"),
    path("stop/", views.stop, name="stop"),
]
