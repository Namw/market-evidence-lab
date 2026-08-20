"""Stable URL-facing facade for the news analysis views.

Display-only endpoints live in feature-focused modules. The execution endpoints
stay here so existing callers and test patch paths remain compatible.
"""

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.news_data.models import NewsRawRecord
from apps.news_data.sources import SUMMARY_ONLY_SOURCE_CODES

from .classification_views import news_observations
from .content import SourceContentError, fetch_source_article, summarize_article_text
from .event_views import (
    event_detail,
    event_list,
    event_overview,
    event_rebuild,
    event_retry_failed,
    event_retry_pair,
    event_run_detail,
    event_run_list,
)
from .models import (
    NewsAnalysisResult,
    NewsAnalysisRun,
    ObjectiveFactExtractionRun,
)
from .objective_fact_views import (
    objective_fact_detail,
    objective_fact_list,
    objective_fact_run_detail,
)
from .objective_facts import (
    BATCH_MODES,
    SINGLE_MODES,
    ObjectiveFactAlreadyRunning,
    get_objective_fact_config,
    objective_fact_selection_count,
    run_objective_fact_extraction,
)
from .services import AnalysisAlreadyRunning, run_news_analysis

@require_GET
def result_content(request, result_id: int):
    result = get_object_or_404(
        NewsAnalysisResult.objects.select_related("news_record__source"),
        pk=result_id,
        status=NewsAnalysisResult.Status.SUCCESS,
    )
    record = result.news_record
    source_url = record.original_url or record.canonical_url
    if record.source.code in SUMMARY_ONLY_SOURCE_CODES:
        return JsonResponse(
            {
                "origin": "saved_summary",
                "content": result.content_summary
                or record.summary
                or "来源暂未提供可显示的摘要。",
                "source_url": source_url,
            }
        )
    try:
        article = fetch_source_article(record)
    except SourceContentError:
        fallback = result.content_summary or record.summary
        return JsonResponse(
            {
                "origin": "saved_summary",
                "content": fallback or "暂未采集到可显示的正文摘要。",
                "source_url": source_url,
            }
        )

    if not result.content_summary:
        result.content_summary = summarize_article_text(article.text)
        result.save(update_fields=["content_summary", "updated_at"])
    return JsonResponse(
        {
            "origin": "source",
            "content": article.text,
            "source_url": article.source_url or source_url,
        }
    )


@require_POST
def run_analysis(request, mode: str):
    if mode not in {
        NewsAnalysisRun.Mode.INCREMENTAL,
        NewsAnalysisRun.Mode.RETRY_FAILED,
    }:
        messages.error(request, "无法识别的新闻分类运行模式。")
        return redirect("news_analysis:index")
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动新闻分类。")
        return redirect("news_analysis:index")
    try:
        run = run_news_analysis(mode=mode, trigger=NewsAnalysisRun.Trigger.MANUAL)
    except AnalysisAlreadyRunning:
        messages.warning(request, "当前分类版本已有运行中的任务，请勿重复启动。")
    except Exception:
        messages.error(request, "新闻分类运行失败，请检查运行日志。")
    else:
        if run.status == NewsAnalysisRun.Status.SUCCESS:
            messages.success(request, "ETH 新闻分类完成。")
        elif run.status == NewsAnalysisRun.Status.PARTIAL:
            messages.warning(request, "ETH 新闻分类部分完成，失败项会在下次重试。")
        else:
            messages.error(request, "ETH 新闻分类未成功完成。")
    return redirect("news_analysis:index")



def _objective_fact_operator(request) -> str:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        username = user.get_username()
        return username or f"user:{user.pk}"
    return "anonymous"


def _batch_mode(mode: str) -> str | None:
    return mode if mode in BATCH_MODES else None


@require_GET
def objective_fact_run_confirm(request, mode: str):
    mode = _batch_mode(mode)
    if mode is None:
        messages.error(request, "无法识别的客观事实批量运行模式。")
        return redirect("news_analysis:objective_fact_list")
    config = get_objective_fact_config()
    candidate_count, skipped_count = objective_fact_selection_count(mode=mode)
    scope_text = {
        ObjectiveFactExtractionRun.Mode.INCREMENTAL: (
            "处理当前版本下没有任何成功提取结果的新闻；旧版本成功结果不会导致跳过。"
        ),
        ObjectiveFactExtractionRun.Mode.RETRY_FAILED: (
            "仅处理当前版本最新结果为模型/解析失败、校验 error 或其他无效执行的新闻；可用 warning 不重试。"
        ),
    }[mode]
    return render(
        request,
        "news_analysis/objective_fact_confirm.html",
        {
            "mode": mode,
            "mode_label": dict(ObjectiveFactExtractionRun.Mode.choices)[mode],
            "prompt_version": config.prompt_version,
            "model": config.model,
            "candidate_count": candidate_count,
            "skipped_count": skipped_count,
            "scope_text": scope_text,
            "api_configured": bool(settings.NEWS_AI_API_KEY),
            "active_run": ObjectiveFactExtractionRun.objects.filter(
                status=ObjectiveFactExtractionRun.Status.RUNNING
            ).first(),
        },
    )


@require_POST
def objective_fact_run(request, mode: str):
    mode = _batch_mode(mode)
    if mode is None or request.POST.get("confirm") != "yes":
        messages.error(request, "客观事实提取确认参数无效。")
        return redirect("news_analysis:objective_fact_list")
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动客观事实提取。")
        return redirect("news_analysis:objective_fact_run_confirm", mode=mode)
    try:
        run = run_objective_fact_extraction(
            mode=mode,
            trigger=ObjectiveFactExtractionRun.Trigger.MANUAL,
            triggered_by=_objective_fact_operator(request),
        )
    except ObjectiveFactAlreadyRunning:
        messages.warning(request, "当前已有客观事实提取任务正在运行，请勿重复启动。")
        return redirect("news_analysis:objective_fact_list")
    except Exception:
        messages.error(request, "客观事实提取未能启动，请检查运行记录和服务日志。")
        return redirect("news_analysis:objective_fact_list")
    return redirect("news_analysis:objective_fact_run_detail", run_id=run.id)
@require_POST
def objective_fact_single_run(request, news_id: int, mode: str):
    record = get_object_or_404(NewsRawRecord, pk=news_id)
    if mode not in SINGLE_MODES:
        messages.error(request, "无法识别的单条客观事实提取模式。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    try:
        objective_fact_selection_count(mode=mode, record_ids=[record.id])
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    if not settings.NEWS_AI_API_KEY:
        messages.error(request, "DeepSeek API 未配置，无法启动客观事实提取。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    try:
        run = run_objective_fact_extraction(
            mode=mode,
            trigger=ObjectiveFactExtractionRun.Trigger.MANUAL,
            triggered_by=_objective_fact_operator(request),
            record_ids=[record.id],
        )
    except ObjectiveFactAlreadyRunning:
        messages.warning(request, "当前已有客观事实提取任务正在运行，请稍后重试。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    except Exception:
        messages.error(request, "单条客观事实提取未能启动，请检查运行记录。")
        return redirect("news_analysis:objective_fact_detail", news_id=record.id)
    return redirect("news_analysis:objective_fact_run_detail", run_id=run.id)
