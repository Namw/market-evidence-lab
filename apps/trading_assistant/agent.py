from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import httpx
from django.conf import settings
from django.utils import timezone
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from apps.collection.models import SourceNetworkPolicy
from .data import baseline, capture_snapshot, local_iso
from .models import AnalysisTurn
from .schemas import TradingReport
from .tools import make_tools


class AnalysisState(AgentState):
    analysis_turn_id: str


class CallBudget(AgentMiddleware):
    def __init__(self, turn):
        self.turn = turn

    def before_model(self, state, runtime):
        self.turn.refresh_from_db(fields=["usage"])
        count = self.turn.usage.get("model_calls", 0)
        if count >= settings.TRADING_ASSISTANT_MAX_MODEL_CALLS:
            raise ValueError("本轮模型调用已达上限，请缩小问题范围后重新提问。")
        self.turn.usage["model_calls"] = count + 1
        self.turn.progress = "正在结合证据分析" if count else "正在阅读行情与对话"
        self.turn.save(update_fields=["usage", "progress"])


def prepare_turn(turn):
    if not turn.snapshot_id:
        previous = turn.conversation.turns.filter(
            status=AnalysisTurn.Status.SUCCEEDED, snapshot__isnull=False,
            created_at__lt=turn.created_at,
        ).order_by("-created_at").first()
        if not turn.refresh_data and previous:
            turn.snapshot = previous.snapshot
        else:
            turn.snapshot = capture_snapshot(turn.conversation.symbol)
        turn.save(update_fields=["snapshot"])
    if not turn.prompt_text:
        version = settings.TRADING_ASSISTANT_PROMPT_VERSION
        if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+)*", version):
            raise ValueError("提示词版本未安装。请检查 TRADING_ASSISTANT_PROMPT_VERSION。")
        turn.prompt_text = (Path(__file__).parent / "prompts" / f"{version}.md").read_text()
        turn.prompt_version = version
        turn.prompt_hash = hashlib.sha256(turn.prompt_text.encode()).hexdigest()
        turn.model_name = settings.TRADING_ASSISTANT_MODEL
        turn.save(update_fields=["prompt_text", "prompt_version", "prompt_hash", "model_name"])


def context_messages(turn):
    # Reconstruct bounded complete pairs from persisted user-facing reports. This
    # avoids stale tool messages and unfinished calls contaminating the next turn.
    if turn.input_context:
        history = turn.input_context["history"]
        payload = turn.input_context["input"]
    else:
        history, payload = build_input_context(turn)
        turn.input_context = deepcopy({"history": history, "input": payload})
        turn.save(update_fields=["input_context"])
    messages = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
    for old in history:
        messages.append(HumanMessage(content=old["question"]))
        messages.append(AIMessage(content=old["answer"]))
    messages.append(HumanMessage(content=json.dumps(payload, ensure_ascii=False), id=str(turn.pk)))
    return messages


def build_input_context(turn):
    previous = list(turn.conversation.turns.filter(
        status=AnalysisTurn.Status.SUCCEEDED, created_at__lt=turn.created_at,
    ).order_by("-created_at")[:6])
    history = []
    for old in reversed(previous):
        history.append({"question": old.question, "answer": json.dumps({
                "historical_report": old.report,
                "note": "历史报告，仅作会话上下文，不能当成本轮最新证据。",
            }, ensure_ascii=False)})
    last_plan_turn = turn.conversation.turns.filter(
        status=AnalysisTurn.Status.SUCCEEDED, created_at__lt=turn.created_at,
        report__has_key="plans",
    ).exclude(report__plans=[]).order_by("-created_at").first()
    payload = {
        "question": turn.question,
        "conversation_symbol": turn.conversation.symbol,
        "selected_horizon_minutes": turn.horizon_minutes,
        "now": local_iso(timezone.now()),
        "data_mode": "更新行情" if turn.refresh_data else "沿用上次快照，解释原报告",
        "baseline_evidence": baseline(turn.snapshot),
        "last_discussed_plans": {
            "question": last_plan_turn.question,
            "plans": last_plan_turn.report["plans"],
            "cutoff": last_plan_turn.report.get("cutoff"),
            "note": "历史候选方案；用于追问衔接，不是最新价格，也不表示已下单。",
        } if last_plan_turn else None,
    }
    return history, payload


def validated_report(turn, value):
    parsed = value if isinstance(value, TradingReport) else TradingReport.model_validate(value)
    report = parsed.model_dump()
    executions = {item.result.get("evidence_id"): item for item in turn.tool_executions.all() if item.result}
    allowed = {"E0", *executions}
    if not set(report["evidence_ids"]) <= allowed:
        raise ValueError("报告包含不存在的证据引用。")
    plans = []
    for key in dict.fromkeys(report.pop("plan_ids")):
        item = executions.get(key)
        if not item or item.name != "build_trade_plan" or not item.result.get("available"):
            raise ValueError("报告引用了无效的价格方案。")
        plans.append(item.result)
    quality = turn.snapshot.quality
    historical = not turn.refresh_data
    stale = (timezone.now() - turn.snapshot.cutoff).total_seconds() > 300
    guard_notes = list(quality["reasons"])
    if historical:
        guard_notes.append("本轮沿用原快照，仅用于解释历史报告；当前开仓请更新行情。")
    if stale:
        guard_notes.append("数据截止时间已超过 5 分钟，当前开仓请更新行情。")
    if guard_notes:
        report["stance"] = "wait"
        plans = []
    report.update({
        "plans": plans, "guard_notes": guard_notes,
        "symbol": turn.conversation.symbol,
        "snapshot_id": str(turn.snapshot_id), "cutoff": turn.snapshot.cutoff.isoformat(),
        "reference_price": quality.get("reference_price"),
        "win_rate": None, "win_rate_note": quality["win_rate_note"],
        "quality": quality, "prompt_version": turn.prompt_version,
    })
    return report


def run_agent(turn, checkpointer, *, model=None):
    prepare_turn(turn)
    if model is None and not settings.TRADING_ASSISTANT_API_KEY:
        raise ValueError("未配置模型 API Key，请设置 TRADING_ASSISTANT_API_KEY 或 NEWS_AI_API_KEY。")
    policy = SourceNetworkPolicy.objects.filter(source_key="deepseek").first()
    proxy = settings.SOURCE_PROXY_URL if policy and policy.use_proxy else None
    if policy and policy.use_proxy and not proxy:
        raise ValueError("DeepSeek 已启用代理，但 SOURCE_PROXY_URL 未配置。")
    # No tracing backend is configured by this module. Keys are never persisted.
    with httpx.Client(proxy=proxy, trust_env=False, timeout=settings.TRADING_ASSISTANT_TIMEOUT_SECONDS) as client:
        selected_model = model or ChatDeepSeek(
            model=turn.model_name, api_key=settings.TRADING_ASSISTANT_API_KEY,
            api_base=settings.TRADING_ASSISTANT_BASE_URL,
            timeout=settings.TRADING_ASSISTANT_TIMEOUT_SECONDS,
            max_retries=1, max_tokens=4500, temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
            http_client=client,
        )
        graph = create_agent(
            model=selected_model, tools=make_tools(turn), system_prompt=turn.prompt_text,
            response_format=ToolStrategy(TradingReport),
            state_schema=AnalysisState, checkpointer=checkpointer,
            middleware=[CallBudget(turn)], name="market_entry_assistant",
        )
        usage = UsageMetadataCallbackHandler()
        config = {
            "configurable": {"thread_id": f"trading-assistant:{turn.conversation_id}"},
            "recursion_limit": 30, "callbacks": [usage],
            "max_concurrency": 1,
        }
        state = graph.get_state(config)
        is_resume = state.values.get("analysis_turn_id") == str(turn.pk)
        if is_resume and not state.next and state.values.get("structured_response"):
            response = state.values
        else:
            initial = None if is_resume else {
                "messages": context_messages(turn), "analysis_turn_id": str(turn.pk),
                "structured_response": None,
            }
            response = graph.invoke(initial, config, durability="sync")
        turn.refresh_from_db(fields=["usage"])
        turn.usage["token_usage"] = usage.usage_metadata
        turn.save(update_fields=["usage"])
    return validated_report(turn, response.get("structured_response"))
