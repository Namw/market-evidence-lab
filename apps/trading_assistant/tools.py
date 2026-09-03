"""Small composable tools; no arbitrary SQL, eval, filesystem or network tools."""
import hashlib
import json
import threading
from typing import Annotated, Literal

from django.conf import settings
from django.db import close_old_connections, connections
from langchain.tools import tool
from pydantic import Field

from . import data
from .models import AnalysisTurn, ToolExecution


def make_tools(turn):
    snapshot = turn.snapshot

    def execute_inner(name, arguments, fn):
        key = hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()
        existing = ToolExecution.objects.filter(turn=turn, cache_key=key).first()
        if existing and existing.result:
            return existing.result
        if turn.tool_executions.count() >= settings.TRADING_ASSISTANT_MAX_TOOL_CALLS:
            return {"error": "本轮工具调用已达上限，请根据已有证据作答。"}
        AnalysisTurn.objects.filter(pk=turn.pk).update(progress=f"正在查询与计算：{name}")
        try:
            result = fn()
        except ValueError as exc:
            result = {"error": str(exc)}
        record, _ = ToolExecution.objects.get_or_create(
            turn=turn, cache_key=key, defaults={"name": name, "arguments": arguments},
        )
        result = {**result, "evidence_id": f"E{record.pk}", "snapshot_id": str(snapshot.pk), "cutoff": snapshot.cutoff.isoformat()}
        record.result = result
        record.save(update_fields=["result"])
        return result

    def execute(name, arguments, fn):
        # LangGraph dispatches tools to worker threads. Close their thread-local
        # Django connections instead of leaking one per tool invocation.
        background = threading.current_thread() is not threading.main_thread()
        if background:
            close_old_connections()
        try:
            return execute_inner(name, arguments, fn)
        finally:
            if background:
                connections.close_all()

    @tool
    def get_data_quality() -> dict:
        """检查本次固定快照的时效、缺口和盘口覆盖率；不能查询其他币种。"""
        return execute("get_data_quality", {}, lambda: snapshot.quality)

    @tool
    def get_microstructure_summary(
        minutes: Annotated[int, Field(ge=1, le=1440)],
        offset_minutes: Annotated[int, Field(ge=0, le=1439)] = 0,
    ) -> dict:
        """查询本次快照某个时间窗口的价格、主动成交、盘口与价差摘要；offset 表示向前偏移。"""
        return execute("get_microstructure_summary", {"minutes": minutes, "offset_minutes": offset_minutes}, lambda: data.summary(snapshot, minutes, offset_minutes))

    @tool
    def get_market_series(
        minutes: Annotated[int, Field(ge=1, le=1440)],
        bucket_minutes: Literal[1, 5, 15, 30, 60] = 5,
    ) -> dict:
        """获取分段序列，检查价格和买卖强度变化过程；最多 120 段，缺口不会补零。"""
        return execute("get_market_series", {"minutes": minutes, "bucket_minutes": bucket_minutes}, lambda: data.series(snapshot, minutes, bucket_minutes))

    @tool
    def compare_windows(
        recent_minutes: Annotated[int, Field(ge=1, le=720)] = 15,
        previous_minutes: Annotated[int, Field(ge=1, le=720)] = 45,
    ) -> dict:
        """临时计算：对比最新窗口与紧邻的前一窗口，计算每分钟成交额倍数及买入占比变化。"""
        return execute("compare_windows", {"recent_minutes": recent_minutes, "previous_minutes": previous_minutes}, lambda: data.compare(snapshot, recent_minutes, previous_minutes))

    @tool
    def build_trade_plan(
        direction: Literal["long", "short"],
        horizon_minutes: Literal[240, 480, 1440],
        entry_price: Annotated[float | None, Field(gt=0, allow_inf_nan=False)] = None,
    ) -> dict:
        """计算候选开仓/止盈/止损及成本后收益风险比。entry_price 为空时使用参考价；有值时仅是假设限价。必须指定持有周期。"""
        return execute("build_trade_plan", {"direction": direction, "horizon_minutes": horizon_minutes, "entry_price": entry_price}, lambda: data.trade_plan(snapshot, direction=direction, horizon_minutes=horizon_minutes, entry_price=entry_price))

    return [get_data_quality, get_microstructure_summary, get_market_series, compare_windows, build_trade_plan]
