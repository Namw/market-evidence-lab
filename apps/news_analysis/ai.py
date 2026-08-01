from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import httpx

from apps.news_data.models import NewsRawRecord

from .models import NewsAnalysisResult


MAX_RATIONALE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True, slots=True)
class AIItem:
    news_id: int
    observation_result: str
    event_type: str
    impact_scope: str
    importance: str
    rationale: str
    confidence: str


@dataclass(frozen=True, slots=True)
class BatchAnalysis:
    items: tuple[AIItem, ...]
    actual_model_name: str
    usage: TokenUsage
    request_count: int
    retry_count: int


class BatchAnalysisError(Exception):
    def __init__(
        self,
        safe_summary: str,
        *,
        request_count: int = 0,
        retry_count: int = 0,
        usage: TokenUsage | None = None,
        fatal: bool = False,
    ):
        super().__init__(safe_summary)
        self.safe_summary = safe_summary[:500]
        self.request_count = request_count
        self.retry_count = retry_count
        self.usage = usage or TokenUsage()
        self.fatal = fatal


SYSTEM_PROMPT = """你是 Market Evidence Lab 的新闻独立分析器。你只判断新闻本身的观察价值，不解释价格，不预测涨跌，不输出利好或利空。
标题是主要依据，摘要只作辅助。其他资产新闻不能仅因不涉及以太坊就判为噪声。

观察结果：
- noteworthy：可能改变市场预期、风险、供需、制度环境或交易条件的真实事件。
- routine：真实但通常属于日常运营或影响范围有限的信息。
- noise：明确的奖励任务、返佣、答题、抽奖、交易竞赛、拉新或纯营销内容。
- insufficient：标题与摘要不足以确认事件、对象或实际变化。

event_type 只能是：protocol_upgrade, security_incident, regulation_policy, institutional_adoption, ecosystem_development, listing_delisting, trading_rule_change, platform_operation, market_activity, marketing_activity, research_report, other, unclear。
impact_scope 只能是：ethereum, ethereum_ecosystem, crypto_market, exchange, other_asset, unclear。
importance 和 confidence 只能是：high, medium, low。

必须输出一个 JSON 对象，不要输出 Markdown 或额外说明。JSON 示例：
{"items":[{"news_id":123,"observation_result":"noteworthy","event_type":"protocol_upgrade","impact_scope":"ethereum","importance":"high","rationale":"协议升级改变网络规则，值得后续观察。","confidence":"high"}]}
每个输入 ID 必须且只能返回一次；不得增加或遗漏 ID。rationale 使用一句简洁中文，不超过 200 个字符。"""


def build_input_items(records: Iterable[NewsRawRecord]) -> list[dict]:
    return [
        {
            "news_id": record.id,
            "source": record.source.code,
            "source_category": record.source_category,
            "published_at": record.published_at.isoformat(),
            "title": record.title,
            "summary": record.summary,
        }
        for record in records
    ]


def build_request_payload(records: Iterable[NewsRawRecord], model: str) -> dict:
    input_items = build_input_items(records)
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "请分析以下新闻，并按示例输出 JSON：\n"
                + json.dumps({"items": input_items}, ensure_ascii=False),
            },
        ],
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": max(1200, len(input_items) * 320),
    }


def _enum_values(choices) -> set[str]:
    return {value for value, _ in choices}


def validate_response_content(content: str, requested_ids: set[int]) -> tuple[AIItem, ...]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI 返回空内容。")
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("AI 返回的 JSON 无法解析。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("AI 返回缺少 items 数组。")

    observation_values = _enum_values(NewsAnalysisResult.ObservationResult.choices)
    event_values = _enum_values(NewsAnalysisResult.EventType.choices)
    impact_values = _enum_values(NewsAnalysisResult.ImpactScope.choices)
    level_values = _enum_values(NewsAnalysisResult.Level.choices)
    parsed: list[AIItem] = []
    returned_ids: list[int] = []
    required = {
        "news_id",
        "observation_result",
        "event_type",
        "impact_scope",
        "importance",
        "rationale",
        "confidence",
    }
    for raw in payload["items"]:
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("AI 返回条目字段不完整。")
        news_id = raw["news_id"]
        if isinstance(news_id, bool) or not isinstance(news_id, int):
            raise ValueError("AI 返回的 news_id 类型非法。")
        returned_ids.append(news_id)
        if raw["observation_result"] not in observation_values:
            raise ValueError("AI 返回的 observation_result 非法。")
        if raw["event_type"] not in event_values:
            raise ValueError("AI 返回的 event_type 非法。")
        if raw["impact_scope"] not in impact_values:
            raise ValueError("AI 返回的 impact_scope 非法。")
        if raw["importance"] not in level_values or raw["confidence"] not in level_values:
            raise ValueError("AI 返回的重要程度或置信度非法。")
        rationale = raw["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("AI 返回的判断依据为空。")
        rationale = rationale.strip()
        if len(rationale) > MAX_RATIONALE_LENGTH:
            raise ValueError("AI 返回的判断依据过长。")
        parsed.append(
            AIItem(
                news_id=news_id,
                observation_result=raw["observation_result"],
                event_type=raw["event_type"],
                impact_scope=raw["impact_scope"],
                importance=raw["importance"],
                rationale=rationale,
                confidence=raw["confidence"],
            )
        )
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("AI 返回了重复 news_id。")
    returned_set = set(returned_ids)
    if returned_set != requested_ids:
        if returned_set - requested_ids:
            raise ValueError("AI 返回了请求之外的 news_id。")
        raise ValueError("AI 返回遗漏了 news_id。")
    return tuple(parsed)


def _safe_http_error(status_code: int) -> tuple[str, bool, bool]:
    if status_code in {401, 403}:
        return "AI 服务认证失败，请检查配置。", False, True
    if status_code == 402:
        return "AI 服务余额或额度不足。", False, True
    if status_code in {400, 404, 422}:
        return "AI 服务拒绝了模型或请求配置。", False, True
    if status_code == 429:
        return "AI 服务触发限流。", True, False
    if status_code >= 500:
        return "AI 服务暂时不可用。", True, False
    return "AI 服务返回异常状态。", False, True


def _usage_from_payload(payload: object) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    values = []
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key, 0)
        values.append(value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0)
    return TokenUsage(*values)


class DeepSeekNewsClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        http_client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_retries = max(0, max_retries)
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)

    def analyze_batch(
        self, records: list[NewsRawRecord], *, max_requests: int
    ) -> BatchAnalysis:
        if not self.api_key:
            raise BatchAnalysisError("AI 服务未配置。", fatal=True)
        payload = build_request_payload(records, self.model)
        requested_ids = {record.id for record in records}
        attempts = 0
        usage = TokenUsage()
        last_summary = "AI 分析失败。"
        last_fatal = False
        allowed_attempts = min(self.max_retries + 1, max_requests)
        if allowed_attempts <= 0:
            raise BatchAnalysisError("本次运行已达到 API 请求上限。")

        while attempts < allowed_attempts:
            attempts += 1
            response_payload = None
            try:
                response = self.http_client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.RequestError):
                last_summary = "AI 服务网络请求失败或超时。"
                retryable = True
            else:
                if response.status_code >= 400:
                    last_summary, retryable, last_fatal = _safe_http_error(response.status_code)
                else:
                    retryable = True
                    try:
                        response_payload = response.json()
                    except ValueError:
                        last_summary = "AI 服务响应不是合法 JSON。"
                    else:
                        usage += _usage_from_payload(response_payload)
                        try:
                            choices = response_payload.get("choices")
                            if not isinstance(choices, list) or not choices:
                                raise ValueError("AI 响应缺少 choices。")
                            choice = choices[0]
                            if choice.get("finish_reason") == "length":
                                raise ValueError("AI 输出因长度限制被截断。")
                            if choice.get("finish_reason") != "stop":
                                raise ValueError("AI 输出未正常结束。")
                            message = choice.get("message")
                            if not isinstance(message, dict):
                                raise ValueError("AI 响应缺少 message。")
                            items = validate_response_content(
                                message.get("content"), requested_ids
                            )
                        except (AttributeError, TypeError, ValueError) as exc:
                            last_summary = str(exc)[:500]
                        else:
                            actual_model = response_payload.get("model")
                            if not isinstance(actual_model, str):
                                actual_model = ""
                            return BatchAnalysis(
                                items=items,
                                actual_model_name=actual_model,
                                usage=usage,
                                request_count=attempts,
                                retry_count=max(attempts - 1, 0),
                            )
                if last_fatal:
                    retryable = False
            if not retryable:
                break

        raise BatchAnalysisError(
            last_summary,
            request_count=attempts,
            retry_count=max(attempts - 1, 0),
            usage=usage,
            fatal=last_fatal,
        )
