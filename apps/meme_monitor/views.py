from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.meme_monitor.selectors import dashboard_context


@require_GET
def index(request):
    return render(request, "meme_monitor/index.html", dashboard_context())
