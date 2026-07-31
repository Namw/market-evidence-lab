from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import InspectionForm
from .models import KlineInspectionRun
from .services import SUPPORTED_SYMBOL, inspect_klines


def _selected_run(request):
    run_id = request.GET.get("run")
    if not run_id or not run_id.isdigit():
        return None
    return KlineInspectionRun.objects.filter(pk=int(run_id)).first()


@require_http_methods(["GET", "POST"])
def inspection_index(request):
    if request.method == "POST":
        form = InspectionForm(request.POST)
        if form.is_valid():
            runs = []
            for interval in form.cleaned_data["intervals"]:
                runs.append(
                    inspect_klines(
                        SUPPORTED_SYMBOL,
                        interval,
                        form.range_start,
                        form.range_end,
                        trigger=KlineInspectionRun.Trigger.MANUAL,
                    )
                )

            failed_count = sum(
                run.status == KlineInspectionRun.Status.FAILED for run in runs
            )
            issue_count = sum(
                run.quality_status == KlineInspectionRun.QualityStatus.ISSUES
                for run in runs
            )
            if failed_count == len(runs):
                messages.error(request, "所选周期巡检执行失败，请查看错误摘要。")
            elif failed_count:
                messages.warning(request, "部分周期巡检执行失败，其余周期已完成。")
            elif issue_count:
                messages.warning(request, "巡检完成并发现数据质量问题，请查看详情。")
            else:
                messages.success(request, "巡检完成，所选范围未发现质量问题。")
            return redirect("inspection:index")
    else:
        form = InspectionForm()

    context = {
        "form": form,
        "recent_runs": KlineInspectionRun.objects.all()[:20],
        "selected_run": _selected_run(request),
    }
    return render(request, "inspection/index.html", context)
