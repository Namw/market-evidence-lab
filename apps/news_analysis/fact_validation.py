from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, TypeVar

import httpx
from django.conf import settings
from django.db import connection, transaction

from apps.news_data.models import NewsRawRecord

from .models import NewsAnalysisResult, NewsAnalysisRun


RESEARCH_RELEVANCE = {
    "direct",
    "broad_market_candidate",
    "uncertain",
    "irrelevant",
}
CONTENT_TYPES = {
    "concrete_event",
    "follow_up",
    "opinion",
    "interview",
    "educational",
    "prediction",
    "marketing",
    "unknown",
}
FILTER_REASON_CODES = {
    "PASS_DIRECT_EVENT",
    "PASS_BROAD_MARKET_EVENT",
    "INSUFFICIENT_INFORMATION",
    "NO_CONCRETE_EVENT",
    "TOO_INDIRECT_FOR_ETH_RESEARCH",
    "ASSET_OR_COMPANY_SPECIFIC",
    "GENERAL_INDUSTRY_CONTENT",
    "EDUCATIONAL_OR_MARKETING",
    "NON_CRYPTO_OR_ADMINISTRATIVE",
}
PASS_REASON_CODES = {"PASS_DIRECT_EVENT", "PASS_BROAD_MARKET_EVENT"}
TIME_BASES = {"explicit", "inferred", "published_time_only", "unknown"}
SCOPES = {
    "ETH",
    "BTC",
    "stablecoin",
    "regulation",
    "security",
    "macro",
    "broad_crypto",
    "other",
}
BODY_KEYS = (
    "article_text",
    "article_body",
    "body",
    "full_text",
    "content_text",
    "encoded",
)
MAX_STORED_BODY_CHARS = 12_000


FILTER_SYSTEM_PROMPT = """你是 Market Evidence Lab 的新闻研究候选过滤器。任务是逐篇判断输入新闻是否适合作为“ETH 日K异常”的新闻证据候选，而不是判断利好、利空或预测价格。

必须严格遵守：
1. 每篇新闻必须独立判断。不得把同批其他新闻的事实、背景或结论用于当前新闻。
2. 只能使用当前输入的 title、summary、stored_body 和 published_at。不得联网，不得访问 URL，不得使用外部知识、常识补全或未提供的历史背景。
3. source 不会提供给你。不能因为新闻来自 SEC、CFTC、CoinDesk、官方公告或任何特定来源就默认相关或无关。
4. “属于加密行业”不等于适合解释 ETH 日K异常。仅公司经营、单一非 ETH 产品、一般行业观点、教育内容、营销内容、普通执法行政消息或孤立小事件，若没有输入明确支持的 ETH 直接渠道或可信的加密市场系统性渠道，应过滤。
5. 当前信息不足以判断渠道、事件是否真实发生或影响范围时，必须标记 uncertain，不得补全。
6. published_at 仅是文章发布时间，不能证明事件在该时刻发生。
7. 不输出新闻主体、动作、关键事实、客观摘要或任何第二阶段字段。
8. 不得自行创造“可能影响市场信心”“可能影响流动性”“可能影响 DeFi 生态”等通用传导渠道。只有当前标题、摘要或库内正文明确支持直接或系统性渠道，才能使用该渠道。

以下情况本身不足以成为 broad_market_candidate 或 direct：
- 单一协议、跨链桥、钱包、交易所或链上的攻击，仅因其属于 DeFi、跨链、涉及 USDC/USDT/ETH 或可能影响信心而放行；输入必须明确支持对 ETH 网络/市场的直接影响或广泛市场传染、系统性中断。
- ETH 只是被盗资产之一、项目名称含 Ethereum、或项目部署在 Ethereum 上；这不等于 ETH 直接事件。
- 稳定币公司的普通合作、单一产品上下线、一般经营公告；输入必须支持对主要稳定币供给、储备、赎回、系统性基础设施或广泛市场的实质渠道。
- 针对单一公司或产品的监管审批、执法、行政程序；不能仅因监管机构或加密产品出现就视为系统性监管事件。
- BTC 的普通产品、技术指标或行业经营变化；不能仅因 BTC 与 ETH 同属加密资产就推断广泛市场影响。

research_relevance 定义：
- direct：输入明确涉及 ETH、Ethereum 网络、ETH 供需/质押/协议/基础设施/直接监管或直接安全事件，具备作为 ETH 日K异常时间证据的明确渠道。
- broad_market_candidate：输入明确报告可能通过 BTC 主导市场冲击、全市场流动性、重大系统性监管、系统性稳定币、重大行业安全冲击或宏观传导影响广泛加密市场的事件，因而可能解释 ETH 日K异常。仅“与加密有关”不够。
- uncertain：输入可能与 ETH 或广泛加密市场有关，但标题/摘要/库内正文不足以确认直接或系统性渠道，或不足以确认是否存在具体事件。
- irrelevant：输入明确没有可信的 ETH 直接渠道或广泛加密市场系统性渠道，或只是过于间接、资产/公司特定、一般行业、教育、营销、非加密或行政性内容。

content_type 只能是：concrete_event、follow_up、opinion、interview、educational、prediction、marketing、unknown。选择文章的主要内容功能。

should_extract_event 只有同时满足以下两项时才为 true：
A. research_relevance 是 direct 或 broad_market_candidate；
B. 输入明确报告已经发生的具体事件、状态变化、正式决定、正式发布，或对这类事件的后续进展。
观点、采访或预测中即使提到加密市场，也不能仅因“某人发表了观点”而通过；若其中另有明确具体事件，才可通过。

filter_reason_code 只能取：
- PASS_DIRECT_EVENT：直接相关且有具体事件/后续进展；
- PASS_BROAD_MARKET_EVENT：广泛市场候选且有具体事件/后续进展；
- INSUFFICIENT_INFORMATION：现有信息不足；
- NO_CONCRETE_EVENT：主题可能相关，但没有具体事件/后续进展；
- TOO_INDIRECT_FOR_ETH_RESEARCH：与 ETH 日K研究的渠道过于间接；
- ASSET_OR_COMPANY_SPECIFIC：仅涉及特定非 ETH 资产、公司或产品，缺少系统性渠道；
- GENERAL_INDUSTRY_CONTENT：一般行业内容或观点，缺少可用事件渠道；
- EDUCATIONAL_OR_MARKETING：教育、解释或营销内容；
- NON_CRYPTO_OR_ADMINISTRATIVE：非加密内容或普通行政/执法事项。

filter_reason 用一句简洁中文说明当前输入为何得到该结论，不得引用外部背景。每个 news_id 必须且只能返回一次。只输出 JSON，不要输出 Markdown 或解释性前后缀。"""


FILTER_USER_INSTRUCTION = """请返回严格 JSON：
{"items":[{"news_id":123,"research_relevance":"direct","content_type":"concrete_event","filter_reason_code":"PASS_DIRECT_EVENT","filter_reason":"输入明确报告了与 ETH 直接相关的具体事件。","should_extract_event":true}]}

待过滤新闻：
"""


FACT_SYSTEM_PROMPT = """你是新闻事实提取器。输入仅包含已经通过 ETH 日K研究相关性过滤、并被判定含有具体事件或事件后续进展的新闻。请逐篇提取事实，不判断利好、利空或价格方向。

必须严格遵守：
1. 每篇新闻独立处理。不得用同批其他新闻补充当前新闻。
2. 只能使用当前输入的 title、summary、stored_body 和 published_at。不得联网、访问 URL、使用外部知识、常识补全或未提供背景。
3. 只提取输入明确陈述的事实。指控、估计、计划、说法和不确定信息必须保留归因，不得改成已证实事实。
4. published_at 只是文章发布时间，绝不能直接填入 event_occurred_at。
5. event_occurred_at 仅表示主要事件发生时间：explicit 表示输入明确给出日历时间；inferred 表示输入给出相对时间且只借助 published_at 可确定；published_time_only 表示有具体事件但只有文章发布时间可用，此时 event_occurred_at 必须为 null。
6. 所有输入都已通过事件门槛，因此 contains_concrete_event 必须为 true。若时间未知，使用 published_time_only 和 null，不得虚构时间。
7. 无法确定的标量填 null，无法确定的复数字段填空数组。只围绕主要具体事件填写主体、动作、对象，其他明确事实放入 key_facts。

content_type 只能是 concrete_event、follow_up、opinion、interview、educational、prediction、marketing、unknown。event_subjects 是执行主要动作的实体数组；event_action 是简洁动作短语；event_objects 是动作直接作用对象数组。event_occurred_at 使用输入支持的最精确 ISO 8601 表达，不得虚构精度。

involved_scopes 可多选，只能取 ETH、BTC、stablecoin、regulation、security、macro、broad_crypto、other。objective_summary 用简洁中文客观概括输入明确提供的事件，保留必要归因，不作评价或推断。每个 news_id 必须且只能返回一次。只输出 JSON，不要输出 Markdown 或解释性前后缀。"""


FACT_USER_INSTRUCTION = """请返回严格 JSON：
{"items":[{"news_id":123,"content_type":"concrete_event","contains_concrete_event":true,"event_subjects":["主体"],"event_action":"动作","event_objects":["对象"],"event_occurred_at":null,"event_time_basis":"published_time_only","key_facts":["明确事实，保留归因"],"involved_scopes":["ETH"],"objective_summary":"客观摘要"}]}

待提取新闻：
"""


@dataclass(frozen=True, slots=True)
class ArticleInput:
    news_id: int
    title: str
    published_at: str
    summary: str
    stored_body: str


@dataclass(frozen=True, slots=True)
class FilterItem:
    news_id: int
    research_relevance: str
    content_type: str
    filter_reason_code: str
    filter_reason: str
    should_extract_event: bool


@dataclass(frozen=True, slots=True)
class FactItem:
    news_id: int
    content_type: str
    contains_concrete_event: bool
    event_subjects: list[str]
    event_action: str | None
    event_objects: list[str]
    event_occurred_at: str | None
    event_time_basis: str
    key_facts: list[str]
    involved_scopes: list[str]
    objective_summary: str


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AIBatch:
    items: tuple[object, ...]
    actual_model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int
    user_prompt: str


class FactExtractionError(Exception):
    pass


def _text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_text_values(item))
        return result
    if isinstance(value, dict) and set(value).issubset({"text", "attributes"}):
        return _text_values(value.get("text"))
    return []


def database_article_input(record: NewsRawRecord) -> ArticleInput:
    payload = record.raw_payload if isinstance(record.raw_payload, dict) else {}
    body_parts: list[str] = []
    seen: set[str] = set()
    for key in BODY_KEYS:
        for text in _text_values(payload.get(key)):
            normalized = " ".join(text.split())
            marker = normalized.casefold()
            if normalized and marker not in seen and marker != (record.summary or "").casefold():
                seen.add(marker)
                body_parts.append(normalized)
    return ArticleInput(
        news_id=record.id,
        title=record.title,
        published_at=record.published_at.isoformat(),
        summary=record.summary or "",
        stored_body="\n\n".join(body_parts)[:MAX_STORED_BODY_CHARS],
    )


def select_all_records() -> list[NewsRawRecord]:
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        return list(
            NewsRawRecord.objects.select_related("source").order_by(
                "-published_at", "-id"
            )
        )


def business_state_snapshot() -> dict:
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        news = list(
            NewsRawRecord.objects.order_by("id").values_list(
                "id", "updated_at", "content_hash"
            )
        )
        analyses = list(
            NewsAnalysisResult.objects.order_by("id").values_list(
                "id", "updated_at", "status", "conclusion", "analysis_version"
            )
        )
        runs = list(
            NewsAnalysisRun.objects.order_by("id").values_list(
                "id", "updated_at", "status"
            )
        )
    serialized = json.dumps(
        [news, analyses, runs], default=str, separators=(",", ":")
    ).encode()
    return {
        "news_count": len(news),
        "analysis_result_count": len(analyses),
        "analysis_run_count": len(runs),
        "fingerprint": hashlib.sha256(serialized).hexdigest(),
    }


def _build_request(
    items: Iterable[ArticleInput], *, model: str, system_prompt: str, user_instruction: str
) -> tuple[dict, str]:
    serialized = [asdict(item) for item in items]
    user_prompt = user_instruction + json.dumps(
        {"items": serialized}, ensure_ascii=False, separators=(",", ":")
    )
    return (
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max(1800, len(serialized) * 900),
        },
        user_prompt,
    )


def build_filter_request_payload(items: Iterable[ArticleInput], model: str) -> tuple[dict, str]:
    return _build_request(
        items,
        model=model,
        system_prompt=FILTER_SYSTEM_PROMPT,
        user_instruction=FILTER_USER_INSTRUCTION,
    )


def build_fact_request_payload(items: Iterable[ArticleInput], model: str) -> tuple[dict, str]:
    return _build_request(
        items,
        model=model,
        system_prompt=FACT_SYSTEM_PROMPT,
        user_instruction=FACT_USER_INSTRUCTION,
    )


def _load_response_items(content: object) -> list[dict]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI 返回空内容。")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 返回的 JSON 无法解析。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("AI 返回缺少 items 数组。")
    if any(not isinstance(item, dict) for item in payload["items"]):
        raise ValueError("AI 返回条目必须是对象。")
    return payload["items"]


def _validate_ids(returned_ids: list[int], requested_ids: set[int]) -> None:
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("AI 返回了重复 news_id。")
    if set(returned_ids) != requested_ids:
        raise ValueError("AI 返回的 news_id 与请求不一致。")


def validate_filter_response(content: object, requested_ids: set[int]) -> tuple[FilterItem, ...]:
    required = {
        "news_id",
        "research_relevance",
        "content_type",
        "filter_reason_code",
        "filter_reason",
        "should_extract_event",
    }
    parsed: list[FilterItem] = []
    returned_ids: list[int] = []
    for raw in _load_response_items(content):
        if set(raw) != required:
            raise ValueError("过滤结果字段不完整或包含额外字段。")
        news_id = raw["news_id"]
        if isinstance(news_id, bool) or not isinstance(news_id, int):
            raise ValueError("news_id 类型非法。")
        if news_id not in requested_ids:
            raise ValueError("AI 返回了请求之外的 news_id。")
        returned_ids.append(news_id)
        relevance = raw["research_relevance"]
        if relevance not in RESEARCH_RELEVANCE:
            raise ValueError("research_relevance 非法。")
        content_type = raw["content_type"]
        if content_type not in CONTENT_TYPES:
            raise ValueError("content_type 非法。")
        reason_code = raw["filter_reason_code"]
        if reason_code not in FILTER_REASON_CODES:
            raise ValueError("filter_reason_code 非法。")
        reason = raw["filter_reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise ValueError("filter_reason 必须是简洁非空字符串。")
        should_extract = raw["should_extract_event"]
        if not isinstance(should_extract, bool):
            raise ValueError("should_extract_event 必须是布尔值。")
        if should_extract and relevance not in {"direct", "broad_market_candidate"}:
            raise ValueError("uncertain/irrelevant 不能进入事实提取。")
        if should_extract != (reason_code in PASS_REASON_CODES):
            raise ValueError("should_extract_event 与 filter_reason_code 不一致。")
        expected_pass_code = {
            "direct": "PASS_DIRECT_EVENT",
            "broad_market_candidate": "PASS_BROAD_MARKET_EVENT",
        }.get(relevance)
        if should_extract and reason_code != expected_pass_code:
            raise ValueError("通过项的相关性与通过原因码不一致。")
        parsed.append(
            FilterItem(
                news_id=news_id,
                research_relevance=relevance,
                content_type=content_type,
                filter_reason_code=reason_code,
                filter_reason=reason.strip(),
                should_extract_event=should_extract,
            )
        )
    _validate_ids(returned_ids, requested_ids)
    return tuple(parsed)


def _string_list(raw: object, field: str, *, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{field} 必须是字符串数组。")
    values = [item.strip() for item in raw]
    if any(not item for item in values) or len(values) != len(set(values)):
        raise ValueError(f"{field} 不能包含空字符串或重复值。")
    if allowed is not None and not set(values).issubset(allowed):
        raise ValueError(f"{field} 包含非法枚举值。")
    return values


def _nullable_string(raw: object, field: str, *, max_length: int) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} 必须是非空字符串或 null。")
    value = raw.strip()
    if len(value) > max_length:
        raise ValueError(f"{field} 过长。")
    return value


def _publication_date_support(source_text: str, published_date: str) -> str | None:
    if published_date in source_text:
        return "explicit"
    try:
        year, month, day = (int(part) for part in published_date.split("-"))
    except (TypeError, ValueError):
        return None
    month_names = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    month_name = month_names[month - 1]
    explicit_patterns = (
        rf"\b{month_name}\s+{day},\s*{year}\b",
        rf"\b{month_name[:3]}\.?\s+{day},\s*{year}\b",
        rf"\b{day}\s+{month_name}\s+{year}\b",
        rf"{year}年\s*{month}月\s*{day}日",
    )
    if any(re.search(pattern, source_text, re.IGNORECASE) for pattern in explicit_patterns):
        return "explicit"
    if re.search(r"\btoday\b|\bearlier today\b|\bthis day\b|今天|今日", source_text, re.IGNORECASE):
        return "inferred"
    return None


def validate_fact_response(
    content: object,
    requested_ids: set[int],
    article_inputs: dict[int, ArticleInput],
) -> tuple[FactItem, ...]:
    required = {
        "news_id",
        "content_type",
        "contains_concrete_event",
        "event_subjects",
        "event_action",
        "event_objects",
        "event_occurred_at",
        "event_time_basis",
        "key_facts",
        "involved_scopes",
        "objective_summary",
    }
    parsed: list[FactItem] = []
    returned_ids: list[int] = []
    for raw in _load_response_items(content):
        if set(raw) != required:
            raise ValueError("事实提取字段不完整或包含额外字段。")
        news_id = raw["news_id"]
        if isinstance(news_id, bool) or not isinstance(news_id, int):
            raise ValueError("news_id 类型非法。")
        if news_id not in requested_ids:
            raise ValueError("AI 返回了请求之外的 news_id。")
        returned_ids.append(news_id)
        content_type = raw["content_type"]
        if content_type not in CONTENT_TYPES:
            raise ValueError("content_type 非法。")
        if raw["contains_concrete_event"] is not True:
            raise ValueError("第二阶段输入必须包含具体事件。")
        subjects = _string_list(raw["event_subjects"], "event_subjects")
        action = _nullable_string(raw["event_action"], "event_action", max_length=300)
        objects = _string_list(raw["event_objects"], "event_objects")
        occurred_at = _nullable_string(
            raw["event_occurred_at"], "event_occurred_at", max_length=80
        )
        time_basis = raw["event_time_basis"]
        if time_basis not in TIME_BASES - {"unknown"}:
            raise ValueError("第二阶段 event_time_basis 非法。")
        facts = _string_list(raw["key_facts"], "key_facts")
        if len(facts) > 12 or any(len(fact) > 800 for fact in facts):
            raise ValueError("key_facts 数量或单项长度超限。")
        scopes = _string_list(raw["involved_scopes"], "involved_scopes", allowed=SCOPES)
        summary = _nullable_string(
            raw["objective_summary"], "objective_summary", max_length=1200
        )
        if summary is None:
            raise ValueError("通过过滤的新闻必须提供 objective_summary。")
        article_input = article_inputs[news_id]
        if occurred_at is not None:
            published_date = article_input.published_at[:10]
            source_text = "\n".join(
                (article_input.title, article_input.summary, article_input.stored_body)
            )
            if occurred_at[:10] == published_date:
                supported_basis = _publication_date_support(source_text, published_date)
                if supported_basis is None:
                    occurred_at = None
                    time_basis = "published_time_only"
                else:
                    time_basis = supported_basis
        if time_basis == "published_time_only" and occurred_at is not None:
            raise ValueError("published_time_only 时 event_occurred_at 必须为 null。")
        if time_basis in {"explicit", "inferred"} and occurred_at is None:
            raise ValueError("explicit/inferred 必须提供 event_occurred_at。")
        parsed.append(
            FactItem(
                news_id=news_id,
                content_type=content_type,
                contains_concrete_event=True,
                event_subjects=subjects,
                event_action=action,
                event_objects=objects,
                event_occurred_at=occurred_at,
                event_time_basis=time_basis,
                key_facts=facts,
                involved_scopes=scopes,
                objective_summary=summary,
            )
        )
    _validate_ids(returned_ids, requested_ids)
    return tuple(parsed)


class TwoStageAIClient:
    def __init__(self, *, http_client: httpx.Client | None = None):
        self.base_url = settings.NEWS_AI_BASE_URL.rstrip("/")
        self.api_key = settings.NEWS_AI_API_KEY
        self.model = settings.NEWS_AI_MODEL
        self.max_retries = max(0, settings.NEWS_AI_MAX_RETRIES)
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=settings.NEWS_AI_TIMEOUT_SECONDS
        )

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def _request(
        self,
        items: list[ArticleInput],
        *,
        builder: Callable[[Iterable[ArticleInput], str], tuple[dict, str]],
        validator: Callable[[object, set[int]], tuple[T, ...]],
    ) -> AIBatch:
        if not self.api_key:
            raise FactExtractionError("NEWS_AI_API_KEY 未配置。")
        payload, user_prompt = builder(items, self.model)
        last_error = "AI 请求失败。"
        for attempt in range(1, self.max_retries + 2):
            try:
                response = self.http_client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                response_payload = response.json()
                choices = response_payload.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ValueError("AI 响应缺少 choices。")
                choice = choices[0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("AI 输出未正常结束或被截断。")
                message = choice.get("message")
                if not isinstance(message, dict):
                    raise ValueError("AI 响应缺少 message。")
                parsed = validator(message.get("content"), {item.news_id for item in items})
                usage = response_payload.get("usage") or {}
                return AIBatch(
                    items=parsed,
                    actual_model=(
                        response_payload.get("model")
                        if isinstance(response_payload.get("model"), str)
                        else ""
                    ),
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    request_count=attempt,
                    user_prompt=user_prompt,
                )
            except (httpx.HTTPError, TypeError, ValueError) as exc:
                last_error = str(exc)[:500]
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
                    400,
                    401,
                    402,
                    403,
                    404,
                    422,
                }:
                    break
        raise FactExtractionError(last_error)

    def filter(self, items: list[ArticleInput]) -> AIBatch:
        return self._request(
            items,
            builder=build_filter_request_payload,
            validator=validate_filter_response,
        )

    def extract(self, items: list[ArticleInput]) -> AIBatch:
        inputs_by_id = {item.news_id: item for item in items}
        return self._request(
            items,
            builder=build_fact_request_payload,
            validator=lambda content, ids: validate_fact_response(
                content, ids, inputs_by_id
            ),
        )


def _chunks(items: list[ArticleInput], size: int) -> Iterable[list[ArticleInput]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _ai_metadata(
    batches: list[AIBatch], *, system_prompt: str, user_instruction: str
) -> dict:
    return {
        "configured_model": settings.NEWS_AI_MODEL,
        "actual_models": sorted(
            {batch.actual_model for batch in batches if batch.actual_model}
        ),
        "request_count_including_retries": sum(batch.request_count for batch in batches),
        "input_tokens": sum(batch.input_tokens for batch in batches),
        "output_tokens": sum(batch.output_tokens for batch in batches),
        "total_tokens": sum(batch.total_tokens for batch in batches),
        "complete_prompt": {
            "system": system_prompt,
            "user_instruction": user_instruction,
            "actual_user_prompts_by_batch": [batch.user_prompt for batch in batches],
        },
    }


def run_fact_validation(*, batch_size: int = 6) -> dict:
    before = business_state_snapshot()
    records = select_all_records()
    if not records:
        raise FactExtractionError("数据库中没有新闻。")
    article_inputs = [database_article_input(record) for record in records]
    inputs_by_id = {item.news_id: item for item in article_inputs}
    safe_batch_size = max(1, min(batch_size, 10))
    client = TwoStageAIClient()
    filter_batches: list[AIBatch] = []
    fact_batches: list[AIBatch] = []
    try:
        for batch_items in _chunks(article_inputs, safe_batch_size):
            filter_batches.append(client.filter(batch_items))
        filter_items = [
            item for batch in filter_batches for item in batch.items
        ]
        filters_by_id = {item.news_id: item for item in filter_items}
        extraction_inputs = [
            inputs_by_id[record.id]
            for record in records
            if filters_by_id[record.id].should_extract_event
        ]
        for batch_items in _chunks(extraction_inputs, safe_batch_size):
            fact_batches.append(client.extract(batch_items))
        fact_items = [item for batch in fact_batches for item in batch.items]
    finally:
        client.close()

    facts_by_id = {item.news_id: item for item in fact_items}
    source_counts = Counter(record.source.code for record in records)
    source_names = {record.source.code: record.source.name for record in records}
    source_relevance: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        source_relevance[record.source.code][
            filters_by_id[record.id].research_relevance
        ] += 1

    report_items = []
    filtered_items = []
    extracted_items = []
    for record in records:
        filter_item = filters_by_id[record.id]
        extraction = facts_by_id.get(record.id)
        common = {
            "news_id": record.id,
            "source_code": record.source.code,
            "source_name": record.source.name,
            "title": record.title,
            "published_at": record.published_at.isoformat(),
            "original_url": record.original_url,
        }
        report_items.append(
            {
                **common,
                "database_input": asdict(inputs_by_id[record.id]),
                "filter": asdict(filter_item),
                "extraction": asdict(extraction) if extraction else None,
            }
        )
        compact = {**common, "filter": asdict(filter_item)}
        if extraction is None:
            filtered_items.append(compact)
        else:
            extracted_items.append({**compact, "extraction": asdict(extraction)})

    after = business_state_snapshot()
    source_summary = {}
    for source_code in sorted(source_counts):
        counts = source_relevance[source_code]
        source_summary[source_code] = {
            "source_name": source_names[source_code],
            "original_count": source_counts[source_code],
            **{
                relevance: counts.get(relevance, 0)
                for relevance in (
                    "direct",
                    "broad_market_candidate",
                    "uncertain",
                    "irrelevant",
                )
            },
        }

    return {
        "report_kind": "temporary_all_news_two_stage_eth_research_validation",
        "generated_at": datetime.now().astimezone().isoformat(),
        "read_only_guarantee": (
            "Business tables are queried in read-only transactions. AI outputs are written only "
            "to this JSON report; no news, analysis result, model, page, or research case is created."
        ),
        "selection": {
            "scope": "all current NewsRawRecord rows",
            "total_processed": len(records),
            "ordering": ["published_at DESC", "id DESC"],
            "database_text_only": True,
            "records_with_stored_body": sum(bool(item.stored_body) for item in article_inputs),
            "records_without_stored_body": sum(not item.stored_body for item in article_inputs),
        },
        "statistics": {
            "total_processed": len(records),
            "source_summary": source_summary,
            "final_event_extraction_count": len(fact_items),
            "filtered_count": len(filtered_items),
            "overall_relevance_counts": dict(
                Counter(item.research_relevance for item in filter_items)
            ),
        },
        "ai": {
            "stage_one_filter": _ai_metadata(
                filter_batches,
                system_prompt=FILTER_SYSTEM_PROMPT,
                user_instruction=FILTER_USER_INSTRUCTION,
            ),
            "stage_two_fact_extraction": _ai_metadata(
                fact_batches,
                system_prompt=FACT_SYSTEM_PROMPT,
                user_instruction=FACT_USER_INSTRUCTION,
            ),
            "conservative_time_normalization": (
                "If an event date equals the publication date but that ISO date does not occur in "
                "the stored input text, event_occurred_at is cleared and event_time_basis becomes "
                "published_time_only."
            ),
        },
        "business_data_verification": {
            "before": before,
            "after": after,
            "unchanged": before == after,
        },
        "filtered_items": filtered_items,
        "extracted_items": extracted_items,
        "items": report_items,
    }


def write_report(report: dict, output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
