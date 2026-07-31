from django.urls import path

from . import views

app_name = "research_cases"

urlpatterns = [
    path("", views.case_list, name="list"),
    path("from-finding/<int:finding_id>/", views.create_from_finding, name="create_from_finding"),
    path("<int:case_id>/", views.case_detail, name="detail"),
]
