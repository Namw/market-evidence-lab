from django.urls import path

from . import views


app_name = "market_funds"

urlpatterns = [
    path("", views.index, name="index"),
    path(
        "stablecoins/",
        views.legacy_section_redirect,
        {"section": "stablecoins"},
        name="stablecoins",
    ),
    path(
        "etf-flows/",
        views.legacy_section_redirect,
        {"section": "etf-flows"},
        name="etf_flows",
    ),
    path("addresses/", views.addresses, name="addresses"),
    path("runs/<int:run_id>/", views.run_detail, name="run_detail"),
]
