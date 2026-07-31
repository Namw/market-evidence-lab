from __future__ import annotations

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.collection.models import CollectionRun
from apps.collection.pipeline import collect_and_inspect
from apps.inspection.models import NewsInspectionRun

from .models import NewsCollectionDiagnostic, NewsRawRecord, NewsSource


@require_GET
def news_collection(request):
    sources = list(NewsSource.objects.all())
    recent_runs = list(
        CollectionRun.objects.filter(data_type=CollectionRun.DataType.NEWS)
        .select_related("news_source")
        .prefetch_related(
            Prefetch(
                "news_diagnostics",
                queryset=NewsCollectionDiagnostic.objects.order_by(
                    "-request_started_at", "-id"
                ),
                to_attr="display_diagnostics",
            ),
            Prefetch(
                "news_inspections",
                queryset=NewsInspectionRun.objects.order_by("-started_at"),
                to_attr="display_inspections",
            ),
        )[:20]
    )
    for run in recent_runs:
        run.display_diagnostic = (
            run.display_diagnostics[0] if run.display_diagnostics else None
        )
        run.display_inspection = (
            run.display_inspections[0] if run.display_inspections else None
        )
    return render(
        request,
        "news_data/index.html",
        {
            "sources": sources,
            "recent_runs": recent_runs,
            "recent_records": NewsRawRecord.objects.select_related("source")[:30],
        },
    )


@require_POST
def run_news_collection(request, source_code: str):
    source = get_object_or_404(NewsSource, code=source_code, enabled=True)
    result = collect_and_inspect(
        data_type=CollectionRun.DataType.NEWS,
        source_code=source.code,
        trigger=CollectionRun.Trigger.MANUAL,
    )
    inspection = result.inspection_run
    if inspection.quality_status == NewsInspectionRun.QualityStatus.PASSED:
        if inspection.inserted_count == 0 and inspection.updated_count == 0:
            messages.success(
                request,
                f"{source.name} 采集与检查通过：来源有可解析内容，本次零新增。",
            )
        else:
            messages.success(request, f"{source.name} 采集与检查通过。")
    elif inspection.quality_status == NewsInspectionRun.QualityStatus.WARNING:
        messages.warning(
            request,
            f"{source.name} 覆盖完整但存在警告；可信水位已按规则推进。",
        )
    else:
        messages.error(
            request,
            f"{source.name} 本次检查失败，可信水位未推进。",
        )
    return redirect("news_data:index")
