from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.scheduling.models import FundDataWorkflowRun

from .models import SourceDiagnostic
from .selectors import address_metrics, etf_metrics, stablecoin_metrics


def _diagnostics():
    return {item.source: item for item in SourceDiagnostic.objects.all()}


def _chart_series(items, *, date_field, value_field):
    return [
        {
            "date": getattr(item, date_field).isoformat(),
            "value": str(getattr(item, value_field)),
        }
        for item in items
        if getattr(item, value_field) is not None
    ]


def _contribution_groups(contributions):
    available = [item for item in contributions if item.flow_usd is not None]
    maximum = max((abs(item.flow_usd) for item in available), default=0)

    def present(items):
        ordered = sorted(items, key=lambda item: abs(item.flow_usd), reverse=True)
        return [
            {
                "ticker": item.ticker,
                "flow_usd": item.flow_usd,
                "bar_percent": f"{abs(item.flow_usd) / maximum * 100:.2f}"
                if maximum
                else "0",
            }
            for item in ordered
        ]

    return {
        "inflows": present([item for item in available if item.flow_usd > 0]),
        "outflows": present([item for item in available if item.flow_usd < 0]),
        "zero": [item.ticker for item in available if item.flow_usd == 0],
        "missing": [item.ticker for item in contributions if item.flow_usd is None],
    }


@require_GET
def index(request):
    stablecoin = stablecoin_metrics()
    etf = etf_metrics()
    context = {
        "active_page": "funds",
        "stablecoin": stablecoin,
        "etf": etf,
        "diagnostics": _diagnostics(),
        "stablecoin_chart_data": _chart_series(
            stablecoin.get("trend", []),
            date_field="observation_date",
            value_field="circulating_supply_usd",
        ),
        "etf_chart_data": _chart_series(
            etf.get("trend", []),
            date_field="trade_date",
            value_field="flow_usd",
        ),
        "etf_contribution_groups": _contribution_groups(
            etf.get("contributions", [])
        ),
    }
    return render(request, "market_funds/index.html", context)


@require_GET
def addresses(request):
    return render(
        request,
        "market_funds/index.html",
        {
            "active_page": "addresses",
            "addresses": address_metrics(),
            "diagnostics": _diagnostics(),
        },
    )


@require_GET
def legacy_section_redirect(request, section):
    return redirect(f"{reverse('market_funds:index')}#{section}")


@require_GET
def run_detail(request, run_id):
    run = get_object_or_404(
        FundDataWorkflowRun.objects.select_related(
            "schedule", "collection_run", "inspection_run"
        ),
        pk=run_id,
    )
    return render(request, "market_funds/run_detail.html", {"run": run})
