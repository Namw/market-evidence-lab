from django.urls import path

from . import views

app_name = "price_evidence"

urlpatterns = [
    path("<int:case_id>/price-evidence/generate/", views.generate, name="generate"),
]
