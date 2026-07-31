from django.urls import path

from . import views

app_name = "inspection"

urlpatterns = [
    path("", views.inspection_index, name="index"),
]
