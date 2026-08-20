from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.inspection.models import DerivativesInspectionRun, KlineInspectionRun

from .models import (
    NewsAIWorkflowRun,
    NewsWorkflowRun,
    SCHEDULE_TIMEZONE,
    WorkflowRun,
)

def step_runs(workflow_run):
    if workflow_run is None:
        return []
    definitions = (
        ("collection_1d", "1d 采集", CollectionRun),
        ("inspection_1d", "1d 巡检", KlineInspectionRun),
        ("collection_1h", "1h 采集", CollectionRun),
        ("inspection_1h", "1h 巡检", KlineInspectionRun),
        ("collection_5m", "5m K线采集", CollectionRun),
        ("inspection_5m", "5m K线巡检", KlineInspectionRun),
        ("collection_oi", "OI 1h 采集", CollectionRun),
        ("inspection_oi", "OI 1h 原始质量检查", DerivativesInspectionRun),
        ("collection_oi_5m", "OI 5m采集", CollectionRun),
        ("inspection_oi_5m", "OI 5m原始质量检查", DerivativesInspectionRun),
        ("collection_funding", "Funding 实际结算采集", CollectionRun),
        (
            "inspection_funding",
            "Funding 实际结算原始质量检查",
            DerivativesInspectionRun,
        ),
    )
    steps = workflow_run.details.get("steps", {})
    status_labels = {
        "pending": "待执行",
        "success": "成功",
        "partial": "部分完成",
        "failed": "失败",
        "not_run": "未执行",
    }
    result = []
    for key, label, model in definitions:
        run_id = workflow_run.details.get(f"{key}_run_id")
        child_run = model.objects.filter(pk=run_id).first() if run_id else None
        is_collection = key.startswith("collection_")
        step = steps.get(key, {})
        result.append(
            {
                "key": key,
                "label": label,
                "step": step,
                "status_label": status_labels.get(
                    step.get("status", "pending"),
                    step.get("status", "pending"),
                ),
                "run": child_run,
                "child_error": (
                    child_run.error_message
                    if child_run is not None and child_run.error_message
                    else ""
                ),
                "is_collection": is_collection,
                "other_issue_count": (
                    child_run.other_issue_count
                    if child_run is not None and not is_collection
                    else 0
                ),
            }
        )
    return result

def workflow_summary(run: WorkflowRun) -> str:
    if run.error_message:
        return run.error_message
    step_errors = [
        step.get("error_summary", "")
        for step in run.details.get("steps", {}).values()
        if step.get("error_summary")
    ]
    if step_errors:
        return "；".join(step_errors)
    if run.status == WorkflowRun.Status.FAILED:
        return "执行失败，但未记录明确错误摘要。请进入详情定位失败步骤。"
    if run.status == WorkflowRun.Status.PARTIAL:
        return "部分步骤未完成，请进入详情查看各步骤状态。"
    if run.quality_status == WorkflowRun.QualityStatus.ISSUES:
        return "执行完成，但数据质量检查发现问题。"
    return "执行完成，未发现异常。"


def news_workflow_summary(run: NewsWorkflowRun) -> str:
    if run.safe_error_summary:
        return run.safe_error_summary
    if run.status == NewsWorkflowRun.Status.FAILED:
        return "执行失败，但未记录明确错误摘要。请进入详情定位失败环节。"
    if run.status == NewsWorkflowRun.Status.PARTIAL:
        return "部分环节未完成，请进入详情查看采集、质量和分析状态。"
    if run.quality_issue_count:
        return f"执行完成，数据质量检查发现 {run.quality_issue_count} 个问题。"
    return "执行完成，未发现异常。"


def news_ai_workflow_summary(run: NewsAIWorkflowRun) -> str:
    if run.safe_error_summary:
        return run.safe_error_summary
    if run.status == NewsAIWorkflowRun.Status.FAILED:
        return "新闻 AI 增量分析失败，请进入详情查看各阶段状态。"
    if run.status == NewsAIWorkflowRun.Status.PARTIAL:
        return "部分 AI 阶段未完成，未处理输入会保留到后续增量运行。"
    return (
        f"执行完成，共 {run.request_count} 次模型请求，"
        f"使用 {run.total_tokens} tokens。"
    )


def run_list_item(run, *, kind: str) -> dict:
    is_market = kind == "market"
    is_news_ai = kind == "news_ai"
    if is_market:
        kind_label = "行情原始数据"
        summary = workflow_summary(run)
    elif is_news_ai:
        kind_label = "新闻 DeepSeek 分析"
        summary = news_ai_workflow_summary(run)
    else:
        kind_label = f"{run.get_feed_group_display()}采集工作流"
        summary = news_workflow_summary(run)
    return {
        "kind": kind,
        "kind_label": kind_label,
        "id": run.pk,
        "trigger": run.get_trigger_display(),
        "status": run.status,
        "status_label": run.get_status_display(),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summary": summary,
        "needs_attention": run.status in {"failed", "partial"}
        or (is_market and run.quality_status == WorkflowRun.QualityStatus.ISSUES)
        or (kind == "news" and run.quality_issue_count > 0),
    }


def run_date_range(request) -> dict:
    schedule_zone = ZoneInfo(SCHEDULE_TIMEZONE)
    today = timezone.localdate(timezone=schedule_zone)
    start_value = request.GET.get("start_date", "").strip()
    end_value = request.GET.get("end_date", "").strip()
    error = ""

    if not start_value and not end_value:
        start_date = end_date = today
    else:
        start_value = start_value or end_value
        end_value = end_value or start_value
        try:
            start_date = date.fromisoformat(start_value)
            end_date = date.fromisoformat(end_value)
        except ValueError:
            start_date = end_date = today
            error = "日期格式无效，已恢复为今日。"
        if start_date > end_date:
            start_date = end_date = today
            error = "开始日期不能晚于结束日期，已恢复为今日。"

    start_at = datetime.combine(start_date, time.min, tzinfo=schedule_zone)
    end_at = datetime.combine(
        end_date + timedelta(days=1),
        time.min,
        tzinfo=schedule_zone,
    )
    if start_date == end_date == today:
        label = "今日采集情况"
    elif start_date == end_date:
        label = f"{start_date:%Y年%m月%d日}采集情况"
    else:
        label = f"{start_date:%Y年%m月%d日} 至 {end_date:%Y年%m月%d日}"
    return {
        "start_date": start_date,
        "end_date": end_date,
        "start_at": start_at,
        "end_at": end_at,
        "today": today,
        "label": label,
        "error": error,
    }
