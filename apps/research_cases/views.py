from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.market_monitoring.models import MarketAnomalyFinding
from apps.derivatives_evidence.models import DerivativesEvidence
from apps.derivatives_evidence.presentation import prepare_derivatives_evidence_for_display
from apps.price_evidence.models import PriceEvidence
from apps.price_evidence.presentation import prepare_price_evidence_for_display

from .models import ResearchCase
from .presentation import prepare_case_for_display
from .services import get_or_create_case_from_finding


def case_list(request):
    cases = list(
        ResearchCase.objects.select_related("source_finding__run").all()
    )
    for research_case in cases:
        prepare_case_for_display(research_case)
    return render(request, "research_cases/list.html", {"research_cases": cases})


def case_detail(request, case_id):
    research_case = get_object_or_404(
        ResearchCase.objects.select_related(
            "source_finding__run",
            "price_evidence",
            "derivatives_evidence",
        ),
        pk=case_id,
    )
    prepare_case_for_display(research_case)
    try:
        price_evidence = research_case.price_evidence
    except PriceEvidence.DoesNotExist:
        price_evidence = None
    if price_evidence is not None:
        prepare_price_evidence_for_display(price_evidence)
    try:
        derivatives_evidence = research_case.derivatives_evidence
    except DerivativesEvidence.DoesNotExist:
        derivatives_evidence = None
    if derivatives_evidence is not None:
        prepare_derivatives_evidence_for_display(derivatives_evidence)
    return render(
        request,
        "research_cases/detail.html",
        {
            "research_case": research_case,
            "price_evidence": price_evidence,
            "derivatives_evidence": derivatives_evidence,
        },
    )


@require_POST
def create_from_finding(request, finding_id):
    finding = get_object_or_404(
        MarketAnomalyFinding.objects.select_related("run"),
        pk=finding_id,
    )
    research_case, created = get_or_create_case_from_finding(finding)
    if created:
        messages.success(request, "研究案例已建立，并保存了创建时的异常与行情快照。")
    else:
        messages.info(request, "该市场在同一 UTC 日期已有研究案例，已为你打开。")
    return redirect(reverse("research_cases:detail", args=[research_case.pk]))
