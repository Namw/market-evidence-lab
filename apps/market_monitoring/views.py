from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import MarketScanForm
from .models import MarketScanRun
from .services import rules_snapshot, scan_market_anomalies


SIGNAL_LABELS = {
    "abnormal_change_up": "大幅上涨",
    "abnormal_change_down": "大幅下跌",
    "volume_spike": "成交量异常",
    "long_upper_wick": "长上影线",
    "long_lower_wick": "长下影线",
}


def _selected_run(request):
    run_id = request.GET.get("run")
    if not run_id or not run_id.isdigit():
        return None
    return MarketScanRun.objects.prefetch_related("findings").filter(pk=int(run_id)).first()


def _findings_for_display(selected_run):
    if selected_run is None:
        return []
    findings = list(selected_run.findings.all())
    for finding in findings:
        finding.signal_badges = [
            {
                "type": signal.get("type", "unknown"),
                "label": SIGNAL_LABELS.get(
                    signal.get("type"),
                    signal.get("type", "未知类型"),
                ),
            }
            for signal in finding.signals
        ]
    return findings


@require_http_methods(["GET", "POST"])
def market_inspection_index(request):
    if request.method == "POST":
        form = MarketScanForm(request.POST)
        if form.is_valid():
            run = scan_market_anomalies(form.range_start, form.range_end)
            if run.status == MarketScanRun.Status.FAILED:
                messages.error(request, "市场异常巡检执行失败，请查看安全错误摘要。")
            elif run.anomaly_day_count:
                messages.warning(
                    request,
                    f"市场异常巡检完成，发现 {run.anomaly_day_count} 个异常日期。",
                )
            else:
                messages.success(request, "市场异常巡检完成，未发现符合V1规则的异常。")
            return redirect(f"/market-inspection/?run={run.pk}")
    else:
        form = MarketScanForm()

    selected_run = _selected_run(request)
    context = {
        "form": form,
        "rules": rules_snapshot(),
        "recent_runs": MarketScanRun.objects.all()[:20],
        "selected_run": selected_run,
        "selected_findings": _findings_for_display(selected_run),
    }
    return render(request, "market_monitoring/index.html", context)
