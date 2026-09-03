"""Bounded recovery of malformed model output using previously saved evidence."""
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from pydantic import ValidationError
from django.utils import timezone

from .schemas import TradingReport

MAX_FORMAT_RETRIES = 2
CORRECTION = (
    "上一条模型回复的工具调用或最终报告格式不正确，已移除该条回复及其错误反馈。"
    "此前成功查询、计算的结果仍在上下文中，请直接复用，不必重复计算。"
    "如需补充证据，请先单独调用查询工具；最终仅调用一次 TradingReport。"
    "TradingReport 参数必须是合法 JSON，包含 stance、horizon_minutes、summary、"
    "long、short、wait、evidence_ids、plan_ids、follow_up。"
    "long、short、wait 分别是包含 assessment、supporting、opposing、condition 的对象。"
    "禁止输出 DSML/XML 标记，不要把多个方向拆成多次 TradingReport 调用。"
)


class ReportGenerationError(ValueError):
    """Safe, user-facing report generation failure."""


def malformed_tail(messages):
    """Return the offending latest AI message index and a safe reason, if any."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage):
            return None  # Do not interpret older conversational answers as this response.
        if not isinstance(message, AIMessage):
            continue
        calls = message.tool_calls
        reports = [call for call in calls if call["name"] == "TradingReport"]
        if message.invalid_tool_calls:
            return index, "工具参数不是合法 JSON"
        if any(not call.get("id") for call in calls) or len({call["id"] for call in calls}) != len(calls):
            return index, "工具调用编号缺失或重复"
        if reports and (len(reports) != 1 or len(calls) != 1):
            return index, "一次返回了多份报告或混合了报告与查询"
        if reports:
            try:
                TradingReport.model_validate(reports[0]["args"])
            except ValidationError:
                return index, "报告字段不符合约定结构"
        elif not calls:
            return index, "未返回约定的结构化报告"
        return None
    return None


class ReportRecovery(AgentMiddleware):
    def __init__(self, turn):
        self.turn = turn

    def repair(self, messages):
        issue = malformed_tail(messages)
        if issue is None:
            return None
        index, reason = issue
        self.turn.refresh_from_db(fields=["usage"])
        retries = self.turn.usage.get("format_retries", 0)
        if retries >= MAX_FORMAT_RETRIES:
            raise ReportGenerationError("模型连续返回了无法解析的报告，自动修复次数已用尽。已保留成功的工具结果，可在调用链中查看后重新提问。")
        retries += 1
        self.turn.usage["format_retries"] = retries
        self.turn.usage.setdefault("format_recovery_events", []).append({
            "attempt": retries, "reason": reason, "recorded_at": timezone.now().isoformat(),
        })
        self.turn.progress = f"报告格式异常，正在自动重新生成（{retries}/{MAX_FORMAT_RETRIES}）"
        self.turn.save(update_fields=["usage", "progress"])
        # Drop the entire faulty response and its feedback. Responding only to
        # parsed tool calls leaves invalid_tool_calls without matching replies,
        # which the provider rejects. Earlier successful tool pairs stay intact.
        return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages[:index], HumanMessage(content=CORRECTION)]

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        repaired = self.repair(state["messages"])
        if repaired is not None:
            return {"messages": repaired, "structured_response": None, "jump_to": "model"}
