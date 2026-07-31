from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.research_cases.models import ResearchCase

from .services import generate_price_evidence


@require_POST
def generate(request, case_id):
    research_case = get_object_or_404(ResearchCase, pk=case_id)
    price_evidence, created = generate_price_evidence(research_case.pk)
    action = "生成" if created else "重新生成"
    messages.success(
        request,
        f"价格证据已{action}，当前数据状态：{price_evidence.get_quality_status_display()}。",
    )
    return redirect(
        f"{reverse('research_cases:detail', args=[research_case.pk])}#price-evidence"
    )
