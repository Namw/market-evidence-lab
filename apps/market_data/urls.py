from django.urls import path

from . import views


app_name = "market_data"

urlpatterns = [
    path("", views.data_view, name="index"),
    path("deribit-options/", views.deribit_options_view, name="deribit_options"),
]
