from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.meme_monitor.scheduling import set_meme_schedule_enabled
from apps.meme_monitor.selectors import (
    anomalies_context,
    overview_context,
    pairs_context,
    research_context,
)


@require_GET
def index(request):
    return render(request, "meme_monitor/index.html", overview_context())


@require_GET
def anomalies(request):
    return render(
        request,
        "meme_monitor/index.html",
        anomalies_context(page_number=request.GET.get("page")),
    )


@require_GET
def pairs(request):
    return render(
        request,
        "meme_monitor/index.html",
        pairs_context(page_number=request.GET.get("page")),
    )


@require_GET
def research(request):
    return render(
        request,
        "meme_monitor/index.html",
        research_context(page_number=request.GET.get("page")),
    )


@require_POST
def toggle_schedule(request):
    requested = request.POST.get("enabled")
    if requested not in {"0", "1"}:
        return HttpResponseBadRequest("invalid schedule state")
    enabled = requested == "1"
    set_meme_schedule_enabled(enabled)
    messages.success(
        request,
        "Meme 定时检查已启用，将由统一调度执行器领取。"
        if enabled
        else "Meme 定时检查已关闭，不再产生新的检查轮次。",
    )
    return redirect("meme_monitor:index")
