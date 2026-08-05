from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping

import httpx

from apps.collection.source_network import source_proxy_url
from apps.news_data.models import NewsRawRecord

from .models import NewsAnalysisResult


MAX_RATIONALE_LENGTH = 200
MAX_SUMMARY_LENGTH = 600
MAX_CONTENT_LENGTH = 12_000


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
    conclusion: str
    rationale: str
    content_summary: str


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


SYSTEM_PROMPT = """你是 Market Evidence Lab 的 ETH 新闻方向分类器。目标是判断单个新闻事件对 ETH 的直接或可信系统性影响，不做价格预测，也不能把一般情绪当作方向证据。

结论只能是：
- bullish：事件明确改善 ETH 的需求、采用、合规入口、可用性、安全性或供给结构。
- bearish：事件明确恶化 ETH 的需求、采用、合规入口、可用性、安全性，或带来直接监管/抛售压力。
- unclear：证据不足、影响相互抵消，或无法从当前输入确定方向。
- irrelevant：与 ETH 没有可信的直接或系统性联系，包括纯营销、答题、返佣、抽奖、交易竞赛和仅涉及其他资产的日常公告。

保守判断：不要因为新闻语气积极就判 bullish，也不要因为出现风险词就自动判 bearish。只有证据明确时才输出方向。
必须输出 JSON，不要输出 Markdown。每个输入 ID 必须且只能返回一次。rationale 使用一句简洁中文，不超过 200 字。content_summary 不超过 600 字；标题阶段没有正文，必须返回空字符串。"""


def build_input_items(
    records: Iterable[NewsRawRecord],
    *,
    stage: str,
    contents: Mapping[int, str] | None = None,
) -> list[dict]:
    items = []
    for record in records:
        item = {
            "news_id": record.id,
            "source": record.source.code,
            "source_category": record.source_category,
            "published_at": record.published_at.isoformat(),
            "title": record.title,
        }
        if stage in {
            NewsAnalysisResult.ClassificationStage.SUMMARY_AI,
            NewsAnalysisResult.ClassificationStage.CONTENT_AI,
        }:
            item["content"] = (contents or {}).get(record.id, "")[:MAX_CONTENT_LENGTH]
        items.append(item)
    return items


def build_request_payload(
    records: Iterable[NewsRawRecord],
    model: str,
    *,
    stage: str = NewsAnalysisResult.ClassificationStage.TITLE_AI,
    contents: Mapping[int, str] | None = None,
) -> dict:
    input_items = build_input_items(records, stage=stage, contents=contents)
    if stage == NewsAnalysisResult.ClassificationStage.TITLE_AI:
        instruction = (
            "只根据标题判断。标题不能明确支持 bullish、bearish 或 irrelevant 时，"
            "必须返回 unclear；不要使用常识补全正文。"
        )
    elif stage == NewsAnalysisResult.ClassificationStage.SUMMARY_AI:
        instruction = (
            "结合标题和 RSS 摘要判断，不得假设或补全网页正文。摘要仍不足或影响混合时"
            "返回 unclear。content_summary 用一句或两句中文概括 RSS 中的事件事实。"
        )
    elif stage == NewsAnalysisResult.ClassificationStage.CONTENT_AI:
        instruction = (
            "结合标题和正文内容判断。正文仍不足或影响混合时返回 unclear。"
            "content_summary 用一句或两句中文概括正文中的事件事实。"
        )
    else:
        raise ValueError("不支持的 AI 分类阶段。")
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": instruction
                + "\n输出格式："
                + '{"items":[{"news_id":123,"conclusion":"bullish","rationale":"判断依据。","content_summary":""}]}'
                + "\n待分类新闻：\n"
                + json.dumps({"items": input_items}, ensure_ascii=False),
            },
        ],
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": max(1000, len(input_items) * 240),
    }


def validate_response_content(content: str, requested_ids: set[int]) -> tuple[AIItem, ...]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI 返回空内容。")
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("AI 返回的 JSON 无法解析。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("AI 返回缺少 items 数组。")

    conclusion_values = {value for value, _ in NewsAnalysisResult.Conclusion.choices}
    parsed: list[AIItem] = []
    returned_ids: list[int] = []
    required = {"news_id", "conclusion", "rationale", "content_summary"}
    for raw in payload["items"]:
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("AI 返回条目字段不完整。")
        news_id = raw["news_id"]
        if isinstance(news_id, bool) or not isinstance(news_id, int):
            raise ValueError("AI 返回的 news_id 类型非法。")
        returned_ids.append(news_id)
        if raw["conclusion"] not in conclusion_values:
            raise ValueError("AI 返回的 ETH 结论非法。")
        rationale = raw["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("AI 返回的判断依据为空。")
        rationale = rationale.strip()
        if len(rationale) > MAX_RATIONALE_LENGTH:
            raise ValueError("AI 返回的判断依据过长。")
        summary = raw["content_summary"]
        if not isinstance(summary, str):
            raise ValueError("AI 返回的正文摘要类型非法。")
        summary = summary.strip()
        if len(summary) > MAX_SUMMARY_LENGTH:
            raise ValueError("AI 返回的正文摘要过长。")
        parsed.append(AIItem(news_id, raw["conclusion"], rationale, summary))

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
        values.append(
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else 0
        )
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
        self.http_client = http_client or httpx.Client(
            timeout=timeout_seconds,
            proxy=source_proxy_url("deepseek") or None,
            trust_env=False,
        )

    def analyze_batch(
        self,
        records: list[NewsRawRecord],
        *,
        max_requests: int,
        stage: str = NewsAnalysisResult.ClassificationStage.TITLE_AI,
        contents: Mapping[int, str] | None = None,
    ) -> BatchAnalysis:
        if not self.api_key:
            raise BatchAnalysisError("AI 服务未配置。", fatal=True)
        payload = build_request_payload(
            records, self.model, stage=stage, contents=contents
        )
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
                    last_summary, retryable, last_fatal = _safe_http_error(
                        response.status_code
                    )
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
