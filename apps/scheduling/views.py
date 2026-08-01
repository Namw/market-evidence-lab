import secrets

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.collection.models import CollectionRun
from apps.inspection.models import DerivativesInspectionRun, KlineInspectionRun

from .forms import KlineScheduleForm, NewsWorkflowScheduleForm
from .models import NewsWorkflowRun, SCHEDULE_TIMEZONE, WorkflowRun
from .news_workflow import (
    NewsWorkflowAlreadyRunning,
    execute_news_workflow,
    get_builtin_news_schedule,
)
from .services import (
    calculate_next_run_at,
    execute_workflow,
    get_builtin_schedule,
    scheduler_status,
)


RUN_TOKEN_SESSION_KEY = "scheduling_manual_run_token"
NEWS_RUN_TOKEN_SESSION_KEY = "scheduling_news_manual_run_token"


def _selected_run(request):
    run_id = request.GET.get("run")
    if not run_id or not run_id.isdigit():
        return None
    return WorkflowRun.objects.select_related("schedule").filter(pk=int(run_id)).first()


def _selected_news_run(request):
    run_id = request.GET.get("news_run")
    if not run_id or not run_id.isdigit():
        return None
    return (
        NewsWorkflowRun.objects.select_related(
            "schedule",
            "ethereum_collection_run",
            "binance_collection_run",
            "ethereum_inspection_run",
            "binance_inspection_run",
            "analysis_run",
        )
        .filter(pk=int(run_id))
        .first()
    )


def _step_runs(workflow_run):
    if workflow_run is None:
        return []
    definitions = (
        ("collection_1d", "1d 采集", CollectionRun),
        ("inspection_1d", "1d 巡检", KlineInspectionRun),
        ("collection_1h", "1h 采集", CollectionRun),
        ("inspection_1h", "1h 巡检", KlineInspectionRun),
        ("collection_oi", "OI 1h 采集", CollectionRun),
        ("inspection_oi", "OI 1h 原始质量检查", DerivativesInspectionRun),
        ("collection_funding", "Funding 实际结算采集", CollectionRun),
        (
            "inspection_funding",
            "Funding 实际结算原始质量检查",
            DerivativesInspectionRun,
        ),
    )
    steps = workflow_run.details.get("steps", {})
    result = []
    for key, label, model in definitions:
        run_id = workflow_run.details.get(f"{key}_run_id")
        child_run = model.objects.filter(pk=run_id).first() if run_id else None
        is_collection = key.startswith("collection_")
        result.append(
            {
                "key": key,
                "label": label,
                "step": steps.get(key, {}),
                "run": child_run,
                "is_collection": is_collection,
                "other_issue_count": (
                    child_run.other_issue_count
                    if child_run is not None and not is_collection
                    else 0
                ),
            }
        )
    return result


def _new_run_token(request, session_key: str) -> str:
    token = secrets.token_urlsafe(24)
    request.session[session_key] = token
    return token


@require_http_methods(["GET", "POST"])
def schedule_index(request):
    schedule = get_builtin_schedule()
    news_schedule = get_builtin_news_schedule()
    form = KlineScheduleForm(instance=schedule)
    news_form = NewsWorkflowScheduleForm(instance=news_schedule)
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
        elif action == "save_news":
            news_form = NewsWorkflowScheduleForm(request.POST, instance=news_schedule)
            if news_form.is_valid():
                updated = news_form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_run_at(updated.run_time)
                updated.save()
                messages.success(request, "新闻每日工作流配置已保存。")
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
        elif action == "run_news":
            submitted_token = request.POST.get("news_run_token", "")
            expected_token = request.session.pop(NEWS_RUN_TOKEN_SESSION_KEY, "")
            if not submitted_token or not secrets.compare_digest(
                submitted_token,
                expected_token,
            ):
                messages.warning(request, "该新闻工作流请求已处理或已失效，未重复执行。")
                return redirect("scheduling:index")
            try:
                run = execute_news_workflow(
                    trigger=NewsWorkflowRun.Trigger.MANUAL,
                    schedule=None,
                )
            except NewsWorkflowAlreadyRunning:
                messages.warning(request, "已有新闻每日工作流正在运行，未重复启动。")
                return redirect("scheduling:index")
            except Exception:
                messages.error(request, "新闻每日工作流发生内部错误，未输出外部响应详情。")
                return redirect("scheduling:index")
            if run.status == NewsWorkflowRun.Status.SUCCESS:
                messages.success(request, "新闻每日工作流执行成功。")
            elif run.status == NewsWorkflowRun.Status.PARTIAL:
                messages.warning(request, "新闻每日工作流部分成功，请分别查看三个环节。")
            else:
                messages.error(request, "新闻每日工作流失败，请分别查看三个环节。")
            return redirect(f"/system/schedules/?news_run={run.pk}#news-workflow-details")
        else:
            messages.error(request, "无法识别的操作。")

    selected_run = _selected_run(request)
    selected_news_run = _selected_news_run(request)
    context = {
        "schedule": schedule,
        "form": form,
        "news_schedule": news_schedule,
        "news_form": news_form,
        "scheduler": scheduler_status(),
        "run_token": _new_run_token(request, RUN_TOKEN_SESSION_KEY),
        "news_run_token": _new_run_token(request, NEWS_RUN_TOKEN_SESSION_KEY),
        "recent_runs": WorkflowRun.objects.select_related("schedule")[:20],
        "recent_news_runs": NewsWorkflowRun.objects.select_related(
            "schedule",
            "ethereum_collection_run",
            "binance_collection_run",
            "ethereum_inspection_run",
            "binance_inspection_run",
            "analysis_run",
        )[:20],
        "selected_run": selected_run,
        "selected_steps": _step_runs(selected_run),
        "selected_news_run": selected_news_run,
    }
    return render(request, "scheduling/index.html", context)
