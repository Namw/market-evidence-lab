import secrets

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.collection.models import CollectionRun
from apps.inspection.models import KlineInspectionRun

from .forms import KlineScheduleForm
from .models import SCHEDULE_TIMEZONE, WorkflowRun
from .services import (
    calculate_next_run_at,
    execute_workflow,
    get_builtin_schedule,
    scheduler_status,
)


RUN_TOKEN_SESSION_KEY = "scheduling_manual_run_token"


def _selected_run(request):
    run_id = request.GET.get("run")
    if not run_id or not run_id.isdigit():
        return None
    return WorkflowRun.objects.select_related("schedule").filter(pk=int(run_id)).first()


def _step_runs(workflow_run):
    if workflow_run is None:
        return []
    definitions = (
        ("collection_1d", "1d 采集", CollectionRun),
        ("inspection_1d", "1d 巡检", KlineInspectionRun),
        ("collection_1h", "1h 采集", CollectionRun),
        ("inspection_1h", "1h 巡检", KlineInspectionRun),
    )
    steps = workflow_run.details.get("steps", {})
    result = []
    for key, label, model in definitions:
        run_id = workflow_run.details.get(f"{key}_run_id")
        result.append(
            {
                "key": key,
                "label": label,
                "step": steps.get(key, {}),
                "run": model.objects.filter(pk=run_id).first() if run_id else None,
                "is_collection": key.startswith("collection_"),
            }
        )
    return result


def _new_run_token(request) -> str:
    token = secrets.token_urlsafe(24)
    request.session[RUN_TOKEN_SESSION_KEY] = token
    return token


@require_http_methods(["GET", "POST"])
def schedule_index(request):
    schedule = get_builtin_schedule()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save":
            form = KlineScheduleForm(request.POST, instance=schedule)
            if form.is_valid():
                updated = form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_run_at(updated.run_time)
                updated.save()
                messages.success(request, "自动任务配置已保存。")
                return redirect("scheduling:index")
        elif action == "run":
            submitted_token = request.POST.get("run_token", "")
            expected_token = request.session.pop(RUN_TOKEN_SESSION_KEY, "")
            if not submitted_token or not secrets.compare_digest(
                submitted_token,
                expected_token,
            ):
                messages.warning(request, "该立即运行请求已处理或已失效，未重复执行。")
                return redirect("scheduling:index")
            run = execute_workflow(
                lookback_days=schedule.lookback_days,
                trigger=WorkflowRun.Trigger.MANUAL,
                schedule=None,
            )
            if run.status == WorkflowRun.Status.FAILED:
                messages.error(request, "工作流执行失败，请查看运行详情。")
            elif run.status == WorkflowRun.Status.PARTIAL:
                messages.warning(request, "工作流部分完成，请查看运行详情。")
            elif run.quality_status == WorkflowRun.QualityStatus.ISSUES:
                messages.warning(request, "工作流执行成功，但数据质量巡检发现问题。")
            else:
                messages.success(request, "工作流执行成功，数据质量巡检通过。")
            return redirect(f"/system/schedules/?run={run.pk}")
        else:
            form = KlineScheduleForm(instance=schedule)
            messages.error(request, "无法识别的操作。")
    else:
        form = KlineScheduleForm(instance=schedule)

    selected_run = _selected_run(request)
    context = {
        "schedule": schedule,
        "form": form,
        "scheduler": scheduler_status(),
        "run_token": _new_run_token(request),
        "recent_runs": WorkflowRun.objects.select_related("schedule")[:20],
        "selected_run": selected_run,
        "selected_steps": _step_runs(selected_run),
    }
    return render(request, "scheduling/index.html", context)

