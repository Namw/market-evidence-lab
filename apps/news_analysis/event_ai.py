from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from django.conf import settings

from apps.collection.source_network import source_proxy_url


RELATIONS = {"same_event", "not_same_event", "uncertain"}
REQUIRED_FIELDS = {
    "relation",
    "confidence",
    "same_event_basis",
    "differences",
    "reason",
    "canonical_title",
    "has_fact_conflict",
}

SYSTEM_PROMPT = """你是 Market Evidence Lab 的同一新闻事件判定器。请只判断两份输入是否描述同一次现实动作或同一次状态变化，而不是判断主题、主体、资产或文本是否相关。

绝对规则：
1. 发布时间只用于候选召回，不是事件发生时间，不得据此认定同一事件。
2. 明确不同的动作对象、交易所、代币、协议、产品、攻击目标或司法对象不是同一事件。
3. 新动作或新阶段（例如 announced、approved、occurred、responded、ruled、settled）不是原事件。
4. 信息不足时返回 uncertain；绝不能为了减少事件数量而猜测合并。
5. 来源类型和权威等级不得参与关系或置信度判断。
6. 只能使用提供的结构化提取结果，不得联网或用外部知识补全事实。
7. canonical_title 只能原样选择左侧或右侧的 event_title；都不合适时返回 null。
8. 同一现实事件的后续报道、累计损失更新、人数或金额更新、持续状态更新，若没有明确出现新的独立动作或稳定标识，应判断为 same_event；金额不同和发布时间不同本身不是新事件。
9. 明确的新交易、新 sweep、新裁定、新回应或新执行动作才是新事件。仅出现“第四次”“最新进展”等表述但无法确认独立动作时返回 uncertain，不得仅凭报道阶段拆分。
10. 置信度用于表达同一次现实事件的确定程度：共享独特主体、对象、稳定标识或同一事故名称，且差异只是后续数值更新时，可以给出 0.90 以上；只有主题或通用动作相似时不得给高置信度。
11. 必须以每份输入的“主要事件骨架”为准。安全事故本身、投资者转移资金、资产价格涨跌、机构回应等即使共享同一背景事故或因果线索，也属于不同现实动作，必须返回 not_same_event。

只返回 JSON 对象：
{"relation":"same_event|not_same_event|uncertain","confidence":0.0,"same_event_basis":[],"differences":[],"reason":"","canonical_title":null,"has_fact_conflict":false}
"""


class EventAIError(Exception):
    def __init__(
        self,
        code: str,
        safe_summary: str,
        *,
        retryable: bool,
        attempts: int = 0,
    ):
        super().__init__(safe_summary)
        self.code = code
        self.safe_summary = safe_summary[:500]
        self.retryable = retryable
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class EventAIResponse:
    result: dict
    structured_response: dict
    attempts: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    actual_model: str


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def validate_event_response(value: object, left: dict, right: dict) -> dict:
    if not isinstance(value, dict) or set(value) != REQUIRED_FIELDS:
        raise EventAIError(
            "INVALID_RESPONSE_SCHEMA",
            "模型返回的同一事件判断结构不完整。",
            retryable=False,
        )
    relation = value.get("relation")
    confidence = value.get("confidence")
    basis = value.get("same_event_basis")
    differences = value.get("differences")
    reason = value.get("reason")
    title = value.get("canonical_title")
    conflict = value.get("has_fact_conflict")
    if relation not in RELATIONS:
        raise EventAIError("INVALID_RELATION", "模型返回了非法关系枚举。", retryable=False)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise EventAIError("INVALID_CONFIDENCE", "模型返回了非法置信度。", retryable=False)
    if not isinstance(basis, list) or any(not isinstance(item, str) for item in basis):
        raise EventAIError("INVALID_BASIS", "模型返回的共同锚点格式非法。", retryable=False)
    if not isinstance(differences, list) or any(not isinstance(item, str) for item in differences):
        raise EventAIError("INVALID_DIFFERENCES", "模型返回的差异格式非法。", retryable=False)
    if not isinstance(reason, str):
        raise EventAIError("INVALID_REASON", "模型返回的判断理由格式非法。", retryable=False)
    if not isinstance(conflict, bool):
        raise EventAIError("INVALID_CONFLICT", "模型返回的冲突标记格式非法。", retryable=False)
    allowed_titles = {
        item.strip()
        for item in (left.get("event_title"), right.get("event_title"))
        if isinstance(item, str) and item.strip()
    }
    if title is not None and (not isinstance(title, str) or title.strip() not in allowed_titles):
        raise EventAIError(
            "UNSUPPORTED_CANONICAL_TITLE",
            "模型生成的规范标题超出了成员提取结果。",
            retryable=False,
        )
    return {
        "relation": relation,
        "confidence": float(confidence),
        "same_event_basis": basis,
        "differences": differences,
        "reason": reason[:4000],
        "canonical_title": title.strip() if isinstance(title, str) else None,
        "has_fact_conflict": conflict,
    }


class DeepSeekEventMergeClient:
    """Use the project's configured DeepSeek chat-completions transport."""

    def __init__(self, *, http_client: httpx.Client | None = None):
        self.base_url = settings.NEWS_AI_BASE_URL.rstrip("/")
        self.model = settings.NEWS_AI_MODEL
        self.api_key = settings.NEWS_AI_API_KEY
        self.max_retries = settings.NEWS_AI_MAX_RETRIES
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=settings.NEWS_AI_TIMEOUT_SECONDS,
            proxy=source_proxy_url("deepseek") or None,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def compare(self, left: dict, right: dict) -> EventAIResponse:
        if not self.api_key:
            raise EventAIError("AI_NOT_CONFIGURED", "DeepSeek API 未配置。", retryable=False)
        user_prompt = json.dumps(
            {"left": left, "right": right}, ensure_ascii=False, separators=(",", ":")
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 1200,
        }
        allowed_attempts = self.max_retries + 1
        last_error: EventAIError | None = None
        for attempt in range(1, allowed_attempts + 1):
            try:
                response = self.http_client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = EventAIError(
                    "AI_TRANSPORT_ERROR",
                    f"模型传输失败：{type(exc).__name__}。",
                    retryable=True,
                    attempts=attempt,
                )
                continue
            if response.status_code >= 400:
                retryable = response.status_code == 429 or response.status_code >= 500
                last_error = EventAIError(
                    f"AI_HTTP_{response.status_code}",
                    f"模型服务返回 HTTP {response.status_code}。",
                    retryable=retryable,
                    attempts=attempt,
                )
                if retryable:
                    continue
                raise last_error
            try:
                response_payload = response.json()
                raw = response_payload["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
                validated = validate_event_response(parsed, left, right)
            except EventAIError as exc:
                exc.attempts = attempt
                raise
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                raise EventAIError(
                    "INVALID_JSON_RESPONSE",
                    f"模型返回无法解析的 JSON：{type(exc).__name__}。",
                    retryable=False,
                    attempts=attempt,
                ) from exc
            usage = response_payload.get("usage") if isinstance(response_payload, dict) else {}
            usage = usage if isinstance(usage, dict) else {}
            prompt_tokens = _nonnegative_int(usage.get("prompt_tokens"))
            completion_tokens = _nonnegative_int(usage.get("completion_tokens"))
            total_tokens = _nonnegative_int(usage.get("total_tokens")) or prompt_tokens + completion_tokens
            actual_model = response_payload.get("model") if isinstance(response_payload, dict) else None
            return EventAIResponse(
                result=validated,
                structured_response=validated,
                attempts=attempt,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                actual_model=actual_model if isinstance(actual_model, str) else self.model,
            )
        if last_error is not None:
            raise last_error
        raise EventAIError("AI_UNKNOWN_ERROR", "模型判断失败。", retryable=True)
