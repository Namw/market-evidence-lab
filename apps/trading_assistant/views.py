import json
import uuid

from django.conf import settings
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .data import HORIZONS, baseline
from .models import AnalysisTurn, Conversation
from .services import submit_turn, worker_online
from .worker_control import WorkerStartError, start_worker
from .trace import execution_trace


@require_http_methods(["GET", "POST"])
def worker(request):
    try:
        if request.method == "POST":
            return JsonResponse(start_worker())
        return JsonResponse({"worker_online": worker_online()})
    except WorkerStartError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except DatabaseError:
        return JsonResponse({"error": "无法读取服务状态，请检查数据库隧道和待执行的迁移。"}, status=503)
    except (OSError, RuntimeError):
        return JsonResponse({"error": "分析服务启动失败，请查看 .local_trading_assistant.log。"}, status=503)


def conversation_payload(item):
    return {"id": str(item.pk), "title": item.title, "symbol": item.symbol, "horizon_minutes": item.horizon_minutes, "updated_at": item.updated_at.isoformat()}


def turn_payload(turn):
    return {
        "id": str(turn.pk), "question": turn.question, "status": turn.status,
        "progress": turn.progress, "report": turn.report, "safe_error": turn.safe_error,
        "created_at": turn.created_at.isoformat(), "refresh_data": turn.refresh_data,
        "horizon_minutes": turn.horizon_minutes,
        "prompt_version": turn.prompt_version, "model_name": turn.model_name,
    }


def read_body(request):
    if len(request.body) > 16000:
        raise ValueError("消息过长。")
    try:
        value = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("请求内容格式不正确。") from None
    if not isinstance(value, dict):
        raise ValueError("请求内容必须是对象。")
    return value


def horizon(value):
    if type(value) is not int or value not in HORIZONS:
        raise ValueError("请选择 4、8 或 24 小时的预期持有时间。")
    return value


@require_GET
def index(request):
    return render(request, "trading_assistant/index.html", {
        "symbols": settings.MICROSTRUCTURE_SYMBOLS,
        "default_symbol": "ZECUSDT" if "ZECUSDT" in settings.MICROSTRUCTURE_SYMBOLS else settings.MICROSTRUCTURE_SYMBOL,
        "ai_configured": bool(settings.TRADING_ASSISTANT_API_KEY),
    })


@require_http_methods(["GET", "POST"])
def conversations(request):
    if request.method == "GET":
        page = Paginator(Conversation.objects.all(), 30).get_page(request.GET.get("page", 1))
        return JsonResponse({"items": [conversation_payload(item) for item in page], "next_page": page.next_page_number() if page.has_next() else None, "worker_online": worker_online()})
    try:
        body = read_body(request)
        symbol = body.get("symbol")
        if symbol not in settings.MICROSTRUCTURE_SYMBOLS:
            raise ValueError("请选择已配置的盘口采集币种。")
        item = Conversation.objects.create(symbol=symbol, horizon_minutes=horizon(body.get("horizon_minutes", 240)))
        return JsonResponse(conversation_payload(item), status=201)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def conversation_detail(request, conversation_id):
    item = get_object_or_404(Conversation, pk=conversation_id)
    page = Paginator(item.turns.order_by("-created_at", "-id"), 20).get_page(request.GET.get("page", 1))
    return JsonResponse({
        "conversation": conversation_payload(item),
        "turns": [turn_payload(turn) for turn in reversed(list(page))],
        "next_page": page.next_page_number() if page.has_next() else None,
        "worker_online": worker_online(),
    })


@require_POST
def send_message(request, conversation_id):
    get_object_or_404(Conversation, pk=conversation_id)
    try:
        body = read_body(request)
        question = body.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError("请输入 1–4000 字的问题。")
        refresh = body.get("refresh_data", True)
        if type(refresh) is not bool:
            raise ValueError("数据更新选项无效。")
        try:
            request_id = uuid.UUID(str(body.get("request_id")))
        except ValueError:
            raise ValueError("请求编号无效，请刷新页面。") from None
        turn = submit_turn(
            conversation_id, question=question.strip(), request_id=request_id,
            refresh_data=refresh, horizon_minutes=horizon(body.get("horizon_minutes", 240)),
        )
        return JsonResponse({"turn": turn_payload(turn), "worker_online": worker_online()}, status=202)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def evidence(request, turn_id):
    turn = get_object_or_404(AnalysisTurn.objects.select_related("snapshot", "conversation"), pk=turn_id)
    executions = list(turn.tool_executions.all())
    baseline_evidence = turn.input_context.get("input", {}).get("baseline_evidence") or (baseline(turn.snapshot) if turn.snapshot_id else None)
    return JsonResponse({
        "trace": execution_trace(turn, executions, baseline_evidence),
        "baseline": baseline_evidence,
        "tools": [{"name": item.name, "arguments": item.arguments, "result": item.result} for item in executions],
        "prompt_version": turn.prompt_version, "prompt_hash": turn.prompt_hash,
        "model": turn.model_name, "usage": turn.usage,
    })
