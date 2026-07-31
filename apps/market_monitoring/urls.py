from django.urls import path

from . import views

app_name = "market_monitoring"

urlpatterns = [
    path("", views.market_inspection_index, name="index"),
]
