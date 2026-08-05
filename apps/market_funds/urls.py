from django.urls import path

from . import views


app_name = "market_funds"

urlpatterns = [
    path("", views.index, name="index"),
    path("stablecoins/", views.index, {"tab": "stablecoins"}, name="stablecoins"),
    path("etf-flows/", views.index, {"tab": "etf-flows"}, name="etf_flows"),
    path("addresses/", views.index, {"tab": "addresses"}, name="addresses"),
    path("runs/<int:run_id>/", views.run_detail, name="run_detail"),
]
