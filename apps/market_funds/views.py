from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.scheduling.models import FundDataWorkflowRun

from .models import SourceDiagnostic
from .selectors import address_metrics, etf_metrics, stablecoin_metrics, svg_polyline


TABS = {
    "overview": "资金概览",
    "stablecoins": "稳定币供应",
    "etf-flows": "ETF 流",
    "addresses": "地址变化",
}


def _diagnostics():
    return {item.source: item for item in SourceDiagnostic.objects.all()}


@require_GET
def index(request, tab="overview"):
    if tab not in TABS:
        tab = "overview"
    stablecoin = stablecoin_metrics()
    etf = etf_metrics()
    addresses = address_metrics()
    context = {
        "tabs": TABS,
        "active_tab": tab,
        "stablecoin": stablecoin,
        "etf": etf,
        "addresses": addresses,
        "diagnostics": _diagnostics(),
        "stablecoin_path": svg_polyline(
            stablecoin.get("trend", []), lambda item: item.circulating_supply_usd
        ),
        "etf_path": svg_polyline(
            etf.get("trend", []), lambda item: item.flow_usd
        ),
    }
    return render(request, "market_funds/index.html", context)


@require_GET
def run_detail(request, run_id):
    run = get_object_or_404(
        FundDataWorkflowRun.objects.select_related(
            "schedule", "collection_run", "inspection_run"
        ),
        pk=run_id,
    )
    return render(request, "market_funds/run_detail.html", {"run": run})
