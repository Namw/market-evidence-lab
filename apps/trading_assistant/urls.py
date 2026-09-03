from django.urls import path

from . import views

app_name = "trading_assistant"
urlpatterns = [
    path("", views.index, name="index"),
    path("api/worker/", views.worker, name="worker"),
    path("api/conversations/", views.conversations, name="conversations"),
    path("api/conversations/<uuid:conversation_id>/", views.conversation_detail, name="conversation_detail"),
    path("api/conversations/<uuid:conversation_id>/messages/", views.send_message, name="send_message"),
    path("api/turns/<uuid:turn_id>/evidence/", views.evidence, name="evidence"),
]
