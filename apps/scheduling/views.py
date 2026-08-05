import secrets
from datetime import date, datetime, time, timedelta
from itertools import chain
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.collection.models import CollectionRun, SourceNetworkPolicy
from apps.collection.source_network import (
    BUILTIN_SOURCES,
    get_source_network_policy,
    safe_proxy_label,
)
from apps.inspection.models import DerivativesInspectionRun, KlineInspectionRun
from apps.news_data.models import NewsFeed, NewsSource

from .deribit_workflow import (
    DeribitOptionsAlreadyRunning,
    execute_manual_deribit_options_workflow,
    get_builtin_deribit_options_schedule,
)
from .funds_workflow import (
    FundWorkflowAlreadyRunning,
    calculate_next_fund_run,
    execute_manual_fund_workflow,
    get_builtin_fund_schedules,
)
from .forms import (
    DeribitOptionsScheduleForm,
    KlineScheduleForm,
    NewsWorkflowScheduleForm,
)
from .models import (
    DeribitOptionsWorkflowRun,
    FundDataWorkflowRun,
    NewsWorkflowRun,
    NewsWorkflowSchedule,
    SCHEDULE_TIMEZONE,
    WorkflowRun,
)
from .news_workflow import (
    NEWS_FEED_GROUP_CODES,
    NewsWorkflowAlreadyRunning,
    execute_news_workflow,
    get_builtin_news_schedule,
)
from .services import (
    calculate_next_interval_run_at,
    calculate_next_run_at,
    execute_workflow,
    get_builtin_schedule,
    scheduler_status,
)


RUN_TOKEN_SESSION_KEY = "scheduling_manual_run_token"
NEWS_RUN_TOKEN_SESSION_KEY = "scheduling_news_manual_run_token"
COINDESK_RUN_TOKEN_SESSION_KEY = "scheduling_coindesk_manual_run_token"
DERIBIT_RUN_TOKEN_SESSION_KEY = "scheduling_deribit_manual_run_token"
FUND_RUN_TOKEN_SESSION_KEY = "scheduling_fund_manual_run_token"


def _step_runs(workflow_run):
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


def _new_run_token(request, session_key: str) -> str:
    token = secrets.token_urlsafe(24)
    request.session[session_key] = token
    return token


def _source_network_rows() -> list[dict]:
    definitions = [
        {
            "key": item.key,
            "name": item.name,
            "category": item.category,
            "endpoint": item.endpoint,
            "note": item.note,
        }
        for item in BUILTIN_SOURCES
    ]
    definitions.extend(
        {
            "key": source.code,
            "name": source.name,
            "category": "新闻采集",
            "endpoint": urlsplit(source.base_url).netloc or source.base_url,
            "note": f"{source.feeds.filter(enabled=True).count()} 个启用栏目",
        }
        for source in NewsSource.objects.filter(enabled=True).order_by("name")
    )
    rows = []
    for definition in definitions:
        policy = get_source_network_policy(definition["key"])
        rows.append({**definition, "policy": policy})
    return rows


@require_http_methods(["GET", "POST"])
def source_network_settings(request):
    rows = _source_network_rows()
    if request.method == "POST":
        valid_keys = {row["key"] for row in rows}
        requested = {
            key: request.POST.get(f"route_{key}", "direct")
            for key in valid_keys
        }
        if any(value not in {"direct", "proxy"} for value in requested.values()):
            messages.error(request, "来源网络策略包含无法识别的连接方式。")
        elif "proxy" in requested.values() and not settings.SOURCE_PROXY_URL:
            messages.error(request, "请先在环境变量中配置 SOURCE_PROXY_URL。")
        else:
            with transaction.atomic():
                for source_key, route in requested.items():
                    SourceNetworkPolicy.objects.update_or_create(
                        source_key=source_key,
                        defaults={"use_proxy": route == "proxy"},
                    )
            messages.success(request, "来源网络策略已保存，后续调度将自动沿用。")
            return redirect("scheduling:sources")
        rows = _source_network_rows()
    return render(
        request,
        "scheduling/source_network.html",
        {
            "source_rows": rows,
            "proxy_configured": bool(settings.SOURCE_PROXY_URL),
            "proxy_label": safe_proxy_label(),
        },
    )


def _workflow_summary(run: WorkflowRun) -> str:
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


def _news_workflow_summary(run: NewsWorkflowRun) -> str:
    if run.safe_error_summary:
        return run.safe_error_summary
    if run.status == NewsWorkflowRun.Status.FAILED:
        return "执行失败，但未记录明确错误摘要。请进入详情定位失败环节。"
    if run.status == NewsWorkflowRun.Status.PARTIAL:
        return "部分环节未完成，请进入详情查看采集、质量和分析状态。"
    if run.quality_issue_count:
        return f"执行完成，数据质量检查发现 {run.quality_issue_count} 个问题。"
    return "执行完成，未发现异常。"


def _run_list_item(run, *, kind: str) -> dict:
    is_market = kind == "market"
    kind_label = (
        "行情原始数据"
        if is_market
        else f"{run.get_feed_group_display()}工作流"
    )
    return {
        "kind": kind,
        "kind_label": kind_label,
        "id": run.pk,
        "trigger": run.get_trigger_display(),
        "status": run.status,
        "status_label": run.get_status_display(),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summary": _workflow_summary(run) if is_market else _news_workflow_summary(run),
        "needs_attention": run.status in {"failed", "partial"}
        or (is_market and run.quality_status == WorkflowRun.QualityStatus.ISSUES)
        or (not is_market and run.quality_issue_count > 0),
    }


def _run_date_range(request) -> dict:
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


@require_http_methods(["GET", "POST"])
def schedule_index(request):
    if request.method == "GET":
        legacy_run_id = request.GET.get("run", "")
        if legacy_run_id.isdigit() and WorkflowRun.objects.filter(
            pk=int(legacy_run_id)
        ).exists():
            return redirect(
                "scheduling:run_detail",
                run_kind="market",
                run_id=int(legacy_run_id),
            )
        legacy_news_run_id = request.GET.get("news_run", "")
        if legacy_news_run_id.isdigit() and NewsWorkflowRun.objects.filter(
            pk=int(legacy_news_run_id)
        ).exists():
            return redirect(
                "scheduling:run_detail",
                run_kind="news",
                run_id=int(legacy_news_run_id),
            )

    schedule = get_builtin_schedule()
    news_schedule = get_builtin_news_schedule(NewsWorkflowSchedule.FeedGroup.CORE)
    coindesk_schedule = get_builtin_news_schedule(
        NewsWorkflowSchedule.FeedGroup.COINDESK
    )
    deribit_schedule = get_builtin_deribit_options_schedule()
    fund_schedules = get_builtin_fund_schedules()
    form = KlineScheduleForm(instance=schedule, auto_id="market_%s")
    news_form = NewsWorkflowScheduleForm(instance=news_schedule, auto_id="news_%s")
    coindesk_form = NewsWorkflowScheduleForm(
        instance=coindesk_schedule,
        auto_id="coindesk_%s",
    )
    deribit_form = DeribitOptionsScheduleForm(
        instance=deribit_schedule,
        auto_id="deribit_%s",
    )
    open_dialog = ""
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save":
            form = KlineScheduleForm(
                request.POST,
                instance=schedule,
                auto_id="market_%s",
            )
            if form.is_valid():
                updated = form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_run_at(updated.run_time)
                updated.save()
                messages.success(request, "自动任务配置已保存。")
                return redirect("scheduling:index")
            open_dialog = "market-config-dialog"
        elif action == "save_news":
            news_form = NewsWorkflowScheduleForm(
                request.POST,
                instance=news_schedule,
                auto_id="news_%s",
            )
            if news_form.is_valid():
                updated = news_form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_interval_run_at(
                    updated.run_time,
                    interval_hours=updated.interval_hours,
                )
                updated.save()
                messages.success(request, "官方与监管新闻工作流配置已保存。")
                return redirect("scheduling:index")
            open_dialog = "news-config-dialog"
        elif action == "save_coindesk":
            coindesk_form = NewsWorkflowScheduleForm(
                request.POST,
                instance=coindesk_schedule,
                auto_id="coindesk_%s",
            )
            if coindesk_form.is_valid():
                updated = coindesk_form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_interval_run_at(
                    updated.run_time,
                    interval_hours=updated.interval_hours,
                )
                updated.save()
                messages.success(request, "CoinDesk 新闻工作流配置已保存。")
                return redirect("scheduling:index")
            open_dialog = "coindesk-config-dialog"
        elif action == "save_deribit":
            deribit_form = DeribitOptionsScheduleForm(
                request.POST,
                instance=deribit_schedule,
                auto_id="deribit_%s",
            )
            if deribit_form.is_valid():
                updated = deribit_form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_run_at(updated.run_time)
                updated.save()
                messages.success(request, "Deribit 期权数据自动任务配置已保存。")
                return redirect("scheduling:index")
            open_dialog = "deribit-config-dialog"
        elif action == "toggle_fund":
            task_type = request.POST.get("task_type", "")
            fund_schedule = next(
                (item for item in fund_schedules if item.task_type == task_type),
                None,
            )
            if fund_schedule is None:
                messages.error(request, "无法识别的 ETH 资金观察任务。")
            elif task_type == "addresses" and not fund_schedule.enabled:
                messages.warning(
                    request,
                    "Etherscan 当前条款阻止自动采集，地址快照任务不能启用。",
                )
            else:
                fund_schedule.enabled = not fund_schedule.enabled
                fund_schedule.next_run_at = calculate_next_fund_run(fund_schedule)
                fund_schedule.save(
                    update_fields=["enabled", "next_run_at", "updated_at"]
                )
                messages.success(
                    request,
                    f"{fund_schedule.name}已{'启用' if fund_schedule.enabled else '停用'}。",
                )
            return redirect("scheduling:index")
        elif action == "run_fund":
            submitted_token = request.POST.get("fund_run_token", "")
            expected_token = request.session.pop(FUND_RUN_TOKEN_SESSION_KEY, "")
            if not submitted_token or not secrets.compare_digest(
                submitted_token,
                expected_token,
            ):
                messages.warning(request, "该资金数据采集请求已处理或已失效，未重复执行。")
                return redirect("scheduling:index")
            task_type = request.POST.get("task_type", "")
            fund_schedule = next(
                (item for item in fund_schedules if item.task_type == task_type),
                None,
            )
            if fund_schedule is None:
                messages.error(request, "无法识别的 ETH 资金观察任务。")
                return redirect("scheduling:index")
            if task_type == "addresses":
                messages.warning(
                    request,
                    "Etherscan 当前条款阻止自动采集，地址快照任务不能执行。",
                )
                return redirect("scheduling:index")
            try:
                run = execute_manual_fund_workflow(task_type)
            except FundWorkflowAlreadyRunning:
                messages.warning(request, f"{fund_schedule.name}正在运行，未重复启动。")
                return redirect("scheduling:index")
            except Exception:
                messages.error(request, "ETH 资金观察任务发生内部错误，未输出外部响应详情。")
                return redirect("scheduling:index")
            if run.status == FundDataWorkflowRun.Status.SUCCESS:
                messages.success(request, f"{fund_schedule.name}采集成功。")
            elif run.status == FundDataWorkflowRun.Status.PARTIAL:
                messages.warning(request, f"{fund_schedule.name}部分完成，请查看运行详情。")
            else:
                messages.error(request, f"{fund_schedule.name}采集失败，请查看运行详情。")
            return redirect("market_funds:run_detail", run_id=run.pk)
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
            return redirect("scheduling:run_detail", run_kind="market", run_id=run.pk)
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
                    feed_group=NewsWorkflowSchedule.FeedGroup.CORE,
                )
            except NewsWorkflowAlreadyRunning:
                messages.warning(request, "已有新闻工作流正在运行，未重复启动。")
                return redirect("scheduling:index")
            except Exception:
                messages.error(request, "官方与监管新闻工作流发生内部错误，未输出外部响应详情。")
                return redirect("scheduling:index")
            if run.status == NewsWorkflowRun.Status.SUCCESS:
                messages.success(request, "官方与监管新闻工作流执行成功。")
            elif run.status == NewsWorkflowRun.Status.PARTIAL:
                messages.warning(request, "官方与监管新闻工作流部分成功，请查看运行详情。")
            else:
                messages.error(request, "官方与监管新闻工作流失败，请查看运行详情。")
            return redirect("scheduling:run_detail", run_kind="news", run_id=run.pk)
        elif action == "run_coindesk":
            submitted_token = request.POST.get("coindesk_run_token", "")
            expected_token = request.session.pop(
                COINDESK_RUN_TOKEN_SESSION_KEY,
                "",
            )
            if not submitted_token or not secrets.compare_digest(
                submitted_token,
                expected_token,
            ):
                messages.warning(request, "该 CoinDesk 工作流请求已处理或已失效，未重复执行。")
                return redirect("scheduling:index")
            try:
                run = execute_news_workflow(
                    trigger=NewsWorkflowRun.Trigger.MANUAL,
                    schedule=None,
                    feed_group=NewsWorkflowSchedule.FeedGroup.COINDESK,
                )
            except NewsWorkflowAlreadyRunning:
                messages.warning(request, "已有新闻工作流正在运行，未重复启动。")
                return redirect("scheduling:index")
            except Exception:
                messages.error(request, "CoinDesk 工作流发生内部错误，未输出外部响应详情。")
                return redirect("scheduling:index")
            if run.status == NewsWorkflowRun.Status.SUCCESS:
                messages.success(request, "CoinDesk 工作流执行成功。")
            elif run.status == NewsWorkflowRun.Status.PARTIAL:
                messages.warning(request, "CoinDesk 工作流部分成功，请查看运行详情。")
            else:
                messages.error(request, "CoinDesk 工作流失败，请查看运行详情。")
            return redirect("scheduling:run_detail", run_kind="news", run_id=run.pk)
        elif action == "run_deribit":
            submitted_token = request.POST.get("deribit_run_token", "")
            expected_token = request.session.pop(DERIBIT_RUN_TOKEN_SESSION_KEY, "")
            if not submitted_token or not secrets.compare_digest(
                submitted_token,
                expected_token,
            ):
                messages.warning(request, "该 Deribit 采集请求已处理或已失效，未重复执行。")
                return redirect("scheduling:index")
            try:
                run = execute_manual_deribit_options_workflow(
                    dvol_lookback_days=deribit_schedule.dvol_lookback_days,
                )
            except DeribitOptionsAlreadyRunning:
                messages.warning(request, "已有 Deribit 期权数据采集正在运行，未重复启动。")
                return redirect("scheduling:index")
            except Exception:
                messages.error(request, "Deribit 期权数据采集发生内部错误，未输出外部响应详情。")
                return redirect("scheduling:index")
            if run.status == DeribitOptionsWorkflowRun.Status.SUCCESS:
                messages.success(request, "Deribit 期权数据采集成功。")
            elif run.status == DeribitOptionsWorkflowRun.Status.PARTIAL:
                messages.warning(request, "Deribit 期权数据采集部分完成，请检查采集记录。")
            else:
                messages.error(request, "Deribit 期权数据采集失败，请检查采集记录。")
            return redirect("scheduling:index")
        else:
            messages.error(request, "无法识别的操作。")

    fund_sources = {
        "stablecoin": "DeFiLlama",
        "etf": "Farside",
        "addresses": "Etherscan（条款阻止）",
    }
    fund_frequency = {
        "stablecoin": "每日 06:00 UTC · 最近 7 天重叠更新",
        "etf": "每日 06:00 / 12:00 UTC · 最近 14 天回刷",
        "addresses": "每日 00:10 UTC · Top 1,000（当前策略阻止）",
    }
    fund_schedule_rows = []
    for item in fund_schedules:
        latest = FundDataWorkflowRun.objects.filter(task_type=item.task_type).first()
        last_success = (
            FundDataWorkflowRun.objects.filter(
                task_type=item.task_type,
                status=FundDataWorkflowRun.Status.SUCCESS,
            )
            .order_by("-finished_at")
            .first()
        )
        fund_schedule_rows.append(
            {
                "schedule": item,
                "source": fund_sources[item.task_type],
                "frequency": fund_frequency[item.task_type],
                "latest": latest,
                "last_success": last_success,
            }
        )

    context = {
        "schedule": schedule,
        "form": form,
        "news_schedule": news_schedule,
        "news_form": news_form,
        "coindesk_schedule": coindesk_schedule,
        "coindesk_form": coindesk_form,
        "deribit_schedule": deribit_schedule,
        "deribit_form": deribit_form,
        "deribit_latest_run": DeribitOptionsWorkflowRun.objects.first(),
        "fund_schedule_rows": fund_schedule_rows,
        "scheduler": scheduler_status(),
        "run_token": _new_run_token(request, RUN_TOKEN_SESSION_KEY),
        "news_run_token": _new_run_token(request, NEWS_RUN_TOKEN_SESSION_KEY),
        "coindesk_run_token": _new_run_token(
            request,
            COINDESK_RUN_TOKEN_SESSION_KEY,
        ),
        "deribit_run_token": _new_run_token(
            request,
            DERIBIT_RUN_TOKEN_SESSION_KEY,
        ),
        "fund_run_token": _new_run_token(request, FUND_RUN_TOKEN_SESSION_KEY),
        "open_dialog": open_dialog,
        "news_feeds": NewsFeed.objects.filter(
            enabled=True,
            source__enabled=True,
            code__in=NEWS_FEED_GROUP_CODES[NewsWorkflowSchedule.FeedGroup.CORE],
        ).select_related("source"),
        "coindesk_feeds": NewsFeed.objects.filter(
            enabled=True,
            source__enabled=True,
            code__in=NEWS_FEED_GROUP_CODES[
                NewsWorkflowSchedule.FeedGroup.COINDESK
            ],
        ).select_related("source"),
    }
    return render(request, "scheduling/index.html", context)


@require_http_methods(["GET"])
def schedule_runs(request):
    selected_task = request.GET.get("task", "all")
    if selected_task not in {"all", "market", "news"}:
        selected_task = "all"

    run_range = _run_date_range(request)
    date_filters = {
        "started_at__gte": run_range["start_at"],
        "started_at__lt": run_range["end_at"],
    }
    market_queryset = WorkflowRun.objects.filter(**date_filters).select_related(
        "schedule"
    )
    news_queryset = NewsWorkflowRun.objects.filter(**date_filters).select_related(
        "schedule"
    )
    market_count = market_queryset.count()
    news_count = news_queryset.count()
    market_runs = list(market_queryset[:100])
    news_runs = list(news_queryset[:100])
    all_items = sorted(
        chain(
            (_run_list_item(run, kind="market") for run in market_runs),
            (_run_list_item(run, kind="news") for run in news_runs),
        ),
        key=lambda item: (item["started_at"], item["id"]),
        reverse=True,
    )
    if selected_task != "all":
        all_items = [item for item in all_items if item["kind"] == selected_task]

    return render(
        request,
        "scheduling/runs.html",
        {
            "run_items": all_items[:100],
            "selected_task": selected_task,
            "market_count": market_count,
            "news_count": news_count,
            "attention_count": sum(item["needs_attention"] for item in all_items),
            "filter_start": run_range["start_date"].isoformat(),
            "filter_end": run_range["end_date"].isoformat(),
            "date_range_label": run_range["label"],
            "filter_error": run_range["error"],
            "display_timezone": SCHEDULE_TIMEZONE,
            "recent_three_start": (
                run_range["today"] - timedelta(days=2)
            ).isoformat(),
            "recent_three_end": run_range["today"].isoformat(),
        },
    )


@require_http_methods(["GET"])
def schedule_run_detail(request, run_kind: str, run_id: int):
    if run_kind == "market":
        run = get_object_or_404(
            WorkflowRun.objects.select_related("schedule"),
            pk=run_id,
        )
        context = {
            "run_kind": run_kind,
            "run": run,
            "summary": _workflow_summary(run),
            "selected_steps": _step_runs(run),
        }
    elif run_kind == "news":
        run = get_object_or_404(
            NewsWorkflowRun.objects.select_related(
                "schedule",
                "ethereum_collection_run",
                "binance_collection_run",
                "ethereum_inspection_run",
                "binance_inspection_run",
                "analysis_run",
            ).prefetch_related(
                "feed_steps__feed__source",
                "feed_steps__collection_run",
                "feed_steps__inspection_run",
            ),
            pk=run_id,
        )
        context = {
            "run_kind": run_kind,
            "run": run,
            "summary": _news_workflow_summary(run),
            "news_feed_steps": run.feed_steps.all(),
        }
    else:
        raise Http404("未知的调度任务类型")
    return render(request, "scheduling/run_detail.html", context)
