from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import MarketScanForm
from .models import MarketScanRun
from .services import rules_snapshot, scan_market_anomalies


def _selected_run(request):
    run_id = request.GET.get("run")
    if not run_id or not run_id.isdigit():
        return None
    return MarketScanRun.objects.prefetch_related("findings").filter(pk=int(run_id)).first()


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
        "selected_findings": selected_run.findings.all() if selected_run else [],
    }
    return render(request, "market_monitoring/index.html", context)

