"""Stable URL-facing facade for scheduling configuration and manual runs.

Run history, detail presentation, and source-network configuration are split
into focused modules. Manual execution stays here to preserve public patch
points used by callers and tests.
"""

import secrets

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.news_data.models import NewsFeed

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
    NewsAIScheduleForm,
    NewsWorkflowScheduleForm,
)
from .models import (
    DeribitOptionsWorkflowRun,
    FundDataWorkflowRun,
    NewsAIWorkflowRun,
    NewsWorkflowRun,
    NewsWorkflowSchedule,
    SCHEDULE_TIMEZONE,
    WorkflowRun,
)
from .news_ai_workflow import (
    NewsAIWorkflowAlreadyRunning,
    execute_news_ai_workflow,
    get_builtin_news_ai_schedule,
)
from .news_workflow import (
    NEWS_FEED_GROUP_CODES,
    NewsWorkflowAlreadyRunning,
    execute_news_workflow,
    get_builtin_news_schedule,
)
from .run_views import schedule_run_detail, schedule_runs
from .services import (
    calculate_next_interval_run_at,
    calculate_next_run_at,
    execute_workflow,
    get_builtin_schedule,
    scheduler_status,
)
from .source_views import source_network_settings

RUN_TOKEN_SESSION_KEY = "scheduling_manual_run_token"
NEWS_RUN_TOKEN_SESSION_KEY = "scheduling_news_manual_run_token"
COINDESK_RUN_TOKEN_SESSION_KEY = "scheduling_coindesk_manual_run_token"
NEWS_AI_RUN_TOKEN_SESSION_KEY = "scheduling_news_ai_manual_run_token"
DERIBIT_RUN_TOKEN_SESSION_KEY = "scheduling_deribit_manual_run_token"
FUND_RUN_TOKEN_SESSION_KEY = "scheduling_fund_manual_run_token"

def _new_run_token(request, session_key: str) -> str:
    token = secrets.token_urlsafe(24)
    request.session[session_key] = token
    return token

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
    news_ai_schedule = get_builtin_news_ai_schedule()
    deribit_schedule = get_builtin_deribit_options_schedule()
    fund_schedules = get_builtin_fund_schedules()
    form = KlineScheduleForm(instance=schedule, auto_id="market_%s")
    news_form = NewsWorkflowScheduleForm(instance=news_schedule, auto_id="news_%s")
    coindesk_form = NewsWorkflowScheduleForm(
        instance=coindesk_schedule,
        auto_id="coindesk_%s",
    )
    news_ai_form = NewsAIScheduleForm(
        instance=news_ai_schedule,
        auto_id="news_ai_%s",
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
        elif action == "save_news_ai":
            news_ai_form = NewsAIScheduleForm(
                request.POST,
                instance=news_ai_schedule,
                auto_id="news_ai_%s",
            )
            if news_ai_form.is_valid():
                updated = news_ai_form.save(commit=False)
                updated.timezone = SCHEDULE_TIMEZONE
                updated.next_run_at = calculate_next_run_at(updated.run_time)
                updated.save()
                messages.success(request, "新闻 DeepSeek 增量分析配置已保存。")
                return redirect("scheduling:index")
            open_dialog = "news-ai-config-dialog"
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
                    run_ai=False,
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
                    run_ai=False,
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
        elif action == "run_news_ai":
            submitted_token = request.POST.get("news_ai_run_token", "")
            expected_token = request.session.pop(NEWS_AI_RUN_TOKEN_SESSION_KEY, "")
            if not submitted_token or not secrets.compare_digest(
                submitted_token,
                expected_token,
            ):
                messages.warning(request, "该新闻 AI 请求已处理或已失效，未重复执行。")
                return redirect("scheduling:index")
            try:
                run = execute_news_ai_workflow(
                    trigger=NewsAIWorkflowRun.Trigger.MANUAL,
                    schedule=None,
                )
            except NewsAIWorkflowAlreadyRunning:
                messages.warning(request, "已有新闻 AI 工作流正在运行，未重复启动。")
                return redirect("scheduling:index")
            except Exception:
                messages.error(request, "新闻 AI 工作流发生内部错误，未输出外部响应详情。")
                return redirect("scheduling:index")
            if run.status == NewsAIWorkflowRun.Status.SUCCESS:
                messages.success(request, "新闻 DeepSeek 增量分析执行成功。")
            elif run.status == NewsAIWorkflowRun.Status.PARTIAL:
                messages.warning(request, "新闻 DeepSeek 增量分析部分成功，请查看详情。")
            else:
                messages.error(request, "新闻 DeepSeek 增量分析失败，请查看详情。")
            return redirect("scheduling:run_detail", run_kind="news_ai", run_id=run.pk)
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
        "stablecoin": "每日 14:00 北京时间 · 最近 7 天重叠更新",
        "etf": "每日 14:00 / 20:00 北京时间 · 最近 14 天回刷",
        "addresses": "每日 08:10 北京时间 · Top 1,000（当前策略阻止）",
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
        "news_ai_schedule": news_ai_schedule,
        "news_ai_form": news_ai_form,
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
        "news_ai_run_token": _new_run_token(
            request,
            NEWS_AI_RUN_TOKEN_SESSION_KEY,
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
