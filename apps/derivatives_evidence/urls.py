from django.urls import path

from . import views


app_name = "derivatives_evidence"

urlpatterns = [
    path("<int:case_id>/derivatives-evidence/generate/", views.generate, name="generate"),
]
