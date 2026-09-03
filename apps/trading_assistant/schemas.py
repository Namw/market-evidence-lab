from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def checkpoint_serializer():
    return JsonPlusSerializer(allowed_msgpack_modules=[
        ("apps.trading_assistant.schemas", "TradingReport"),
        ("apps.trading_assistant.schemas", "Scenario"),
    ])


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: str = Field(max_length=1200, description="中文：该选择是否值得考虑及主要理由")
    supporting: list[str] = Field(max_length=4, description="支持证据；引用工具给出的数值")
    opposing: list[str] = Field(max_length=4, description="反对证据、风险或尚未确认之处")
    condition: str = Field(max_length=800, description="什么具体变化会使这个选择成立或失效")


class TradingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stance: Literal["long", "short", "wait"]
    horizon_minutes: Literal[240, 480, 1440] = Field(default=240, description="实际采用的持有周期，优先本轮用户明确指定，否则采用页面参数")
    summary: str = Field(max_length=2000, description="直接回答本轮问题；明确区分市场事实与推断")
    long: Scenario
    short: Scenario
    wait: Scenario
    evidence_ids: list[str] = Field(min_length=1, max_length=12, description="本轮实际使用的 E 编号；基础摘要为 E0")
    plan_ids: list[str] = Field(default_factory=list, max_length=4, description="仅引用 build_trade_plan 返回的本轮 E 编号；不要自行编造价格方案")
    follow_up: str = Field(max_length=500, description="下一步观察或需要用户补充的信息；无则空字符串")
