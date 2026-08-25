from django.urls import path

from . import views

app_name = "microstructure"

# 注意顺序：静态路由必须放在 <str:symbol> 捕获路由之前。
urlpatterns = [
    path("", views.index, name="index"),
    path("research/", views.research, name="research"),
    path("assistant/", views.assistant_chat, name="assistant_chat"),
    path("status/", views.status, name="status"),
    path("start/", views.start, name="start"),
    path("stop/", views.stop, name="stop"),
    path("<str:symbol>/", views.index, name="index_symbol"),
    path("<str:symbol>/research/", views.research, name="research_symbol"),
    path("<str:symbol>/assistant/", views.assistant_chat, name="assistant_chat_symbol"),
    path("<str:symbol>/status/", views.status, name="status_symbol"),
    path("<str:symbol>/start/", views.start, name="start_symbol"),
    path("<str:symbol>/stop/", views.stop, name="stop_symbol"),
]
