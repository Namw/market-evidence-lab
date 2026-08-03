from __future__ import annotations

import json
import re
from copy import deepcopy
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.db import connection, transaction

from apps.news_data.models import NewsRawRecord

from .fact_validation import business_state_snapshot, database_article_input
from .objective_fact_schema import EVENT_STATUS_PROMPT_TEXT, EVENT_STATUS_VALUES


PROMPT_VERSION = "objective-news-facts-v1.1"
EVENT_STATUSES = frozenset(EVENT_STATUS_VALUES)
INFORMATION_COMPLETENESS = {"sufficient", "partial", "insufficient"}
CLAIM_TYPES = {
    "confirmed_event",
    "company_claim",
    "announced_plan",
    "reported_claim",
    "estimated_or_unconfirmed",
}
AMOUNT_KINDS = {
    "money",
    "crypto_amount",
    "percentage",
    "count",
    "duration",
    "other",
}
GENERATION_PARAMETERS = {
    "stream": False,
    "thinking": {"type": "disabled"},
    "response_format": {"type": "json_object"},
    "temperature": 0,
    "max_tokens": 5000,
}
REQUIRED_RESULT_FIELDS = {
    "event_title",
    "event_time",
    "actors",
    "action",
    "object",
    "event_status",
    "facts",
    "objective_summary",
    "information_completeness",
}
REQUIRED_FACT_FIELDS = {
    "statement",
    "claim_type",
    "evidence_text",
    "fact_time",
    "amounts",
}
REQUIRED_AMOUNT_FIELDS = {"text", "kind"}
ETH_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:ETH|Ethereum)(?![A-Za-z0-9])", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"\d")


SYSTEM_PROMPT = """你是 Market Evidence Lab 的客观新闻事实提取器。你的任务是把单篇新闻中被原始文本直接支持的内容转换成可追溯结构化事实。你不是市场分析器，也不是相关性过滤器。

绝对规则：
1. 只能使用当前这一篇输入中的 source、title、summary、stored_body、published_at、source_category、source_tags、source_author。不得联网、访问 URL、使用外部知识、常识、价格、OI、Funding、研究案例、同批其他新闻或其他来源补全事实。
2. 每次只有一篇新闻。不得与相似新闻合并，也不得借用其他新闻的信息。
3. 不输出利好、利空、重要性、市场影响、价格解释或该新闻是否导致 ETH 涨跌。
4. 不因为主体属于稳定币、银行、监管机构或加密行业，就自行加入 ETH 或 Ethereum。输入没有明确出现 ETH/Ethereum 时，输出也不得出现。
5. 严格区分已经发生、已获批准、主体宣布、申请/提议、未来计划、持续进行、调查中、尚未确认。不得把计划写成已经完成，不得把媒体报道、第三方估计或公司自述写成外部确认事实。
6. 公司对自身储备、安全性、规模、财务数据、市场地位或业务作用的说法必须保留公司归属，claim_type 使用 company_claim；不得改写为外部确认事实。
7. title、summary、stored_body 都是正式事实输入，title 不是仅供定位的元数据。标题本身明确陈述主体、行为、对象、金额、绝对时间、数量或状态时，即使 summary 和 stored_body 为空，也应把标题直接支持的客观事实提取进 facts，并将 evidence_text 逐字取自 title。不得仅因“只有标题”就自动判定 insufficient。
8. 标题只有栏目名、文章类型、观点、问题、修辞、主题概括或演讲名称，而没有明确陈述可验证的事件时，不得把潜在含义、观点或主题推断成客观事实。例如 Op-Ed、Remarks、Keynote、Interview 等标签本身不证明标题讨论的事件已经发生。
9. 同一客观事实同时出现在 title、summary 或 stored_body 时，只保留一条 fact；选择能够完整支持该事实的单一连续 evidence_text，不得因证据字段不同而重复输出相同 statement。
10. 只提取输入文本直接支持的事实。信息不足时，允许字段为 null、数组为空、facts 为空，并使用 information_completeness=insufficient；不得为了填满结构而推断。只有所有输入字段都没有直接支持的客观事实时，才应仅因事实不足返回空 facts。
11. objective_summary 只能复述 facts 中已经提取的内容，不得增加新主体、数字、日期、因果关系、评价或背景。facts 为空时 objective_summary 必须为 null。
12. published_at 只是新闻发布时间，不能直接作为 event_time 或 fact_time。相对词 today/yesterday 也不能借 published_at 转换为绝对时间。只有证据文本本身明确给出绝对日历时间时，才能填写 event_time/fact_time。
13. evidence_text 必须从 title、summary 或 stored_body 的一个字段中复制连续原文片段。除了程序允许统一空白字符，不得改写、翻译、调整大小写、拼接不同字段或进行语义近似。每条事实必须有自己的 evidence_text。
14. amounts 只记录 evidence_text 明确出现的数字表达，格式为 {"text":"原文数字片段","kind":"枚举"}。text 必须逐字出现在 evidence_text 中；不能换算单位、补币种、补小数或生成原文没有的数值。币种、比例和时间周期只有原文明确出现时才记录。

字段规则：
- event_title：中性简短标题；信息不足时为 null。
- event_time：ISO 8601 时间、YYYY-MM、YYYY、YYYY-Q1 等受支持形式，或 null。若非 null，必须与至少一个 fact_time 完全一致。
- actors：执行主要动作的主体字符串数组；不确定时为空数组。
- action：主要动作字符串或 null。
- object：动作直接对象字符串数组；不确定时为空数组。
- event_status 只能是 """ + EVENT_STATUS_PROMPT_TEXT + """。这是事件所处状态，不是消息来源类型；媒体“reported”某事应通过 claim_type=reported_claim 表达，reported 不是 event_status。
- information_completeness 只能是 sufficient、partial、insufficient。
- facts 是事实数组。每项必须包含 statement、claim_type、evidence_text、fact_time、amounts。
- claim_type 只能是：
  * confirmed_event：输入明确记录已完成事件或正式监管/法律结果；
  * company_claim：公司或项目方对自身数据、状态、安全性、规模、作用作出的陈述；
  * announced_plan：主体明确宣布但尚未完成的未来计划；
  * reported_claim：媒体、研究机构、安全机构或其他第三方报告的说法；
  * estimated_or_unconfirmed：估计、约数、传闻、未确认金额或输入明确表示尚未确认。
- fact_time：仅当该条 evidence_text 明确给出绝对时间时填写受支持时间格式，否则为 null。
- amount kind 只能是 money、crypto_amount、percentage、count、duration、other。

只输出 JSON，不要输出 Markdown、注释或前后缀。"""


USER_PROMPT_TEMPLATE = """请严格返回以下结构：
{"event_title":null,"event_time":null,"actors":[],"action":null,"object":[],"event_status":"unknown","facts":[{"statement":"仅由证据支持的事实","claim_type":"reported_claim","evidence_text":"从单一输入字段逐字复制的连续原文","fact_time":null,"amounts":[{"text":"$1.5B","kind":"money"}]}],"objective_summary":null,"information_completeness":"insufficient"}

单篇数据库新闻输入：
"""


class ObjectiveValidationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ObjectiveArticleInput:
    news_id: int
    source: dict[str, str]
    title: str
    summary: str
    stored_body: str
    published_at: str
    source_category: str
    source_tags: list[str]
    source_author: str


def previous_run_audit() -> dict:
    report_path = Path(settings.BASE_DIR) / "validation_reports" / "all_news_two_stage_validation_20260803.json"
    if not report_path.exists():
        raise ObjectiveValidationError("上一轮验证报告不存在，无法确认实际调用链。")
    previous = json.loads(report_path.read_text(encoding="utf-8"))
    stage = previous.get("ai", {}).get("stage_two_fact_extraction", {})
    actual_models = stage.get("actual_models")
    host = urlsplit(settings.NEWS_AI_BASE_URL).hostname or ""
    verified = (
        host.lower() == "api.deepseek.com"
        and stage.get("configured_model") == settings.NEWS_AI_MODEL
        and isinstance(actual_models, list)
        and actual_models
        and all(isinstance(model, str) and model.startswith("deepseek-") for model in actual_models)
    )
    audit = {
        "provider": "DeepSeek" if host.lower() == "api.deepseek.com" else "unknown",
        "provider_host": host,
        "configured_model": stage.get("configured_model"),
        "actual_models_returned_by_api": actual_models,
        "api_chain": [
            "manage.py validate_news_facts",
            "apps.news_analysis.fact_validation.run_fact_validation",
            "TwoStageAIClient.extract",
            "HTTP POST NEWS_AI_BASE_URL/chat/completions",
        ],
        "generation_parameters": {
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        "used_project_configured_deepseek": verified,
        "codex_or_other_model_substitution_detected": False if verified else None,
        "evidence": (
            "The report records the configured and API-returned model; the implementation posts "
            "directly to the configured DeepSeek chat-completions endpoint and has no Codex model path."
        ),
    }
    if not verified:
        raise ObjectiveValidationError(
            "上一轮未能确认使用项目配置的 DeepSeek 调用链，本轮已停止。"
        )
    return audit


def select_records(news_ids: list[int]) -> list[NewsRawRecord]:
    unique_ids = list(dict.fromkeys(news_ids))
    if len(unique_ids) != len(news_ids):
        raise ObjectiveValidationError("news_id 不能重复。")
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        records_by_id = {
            record.id: record
            for record in NewsRawRecord.objects.select_related("source").filter(
                id__in=unique_ids
            )
        }
    missing = [news_id for news_id in unique_ids if news_id not in records_by_id]
    if missing:
        raise ObjectiveValidationError(f"数据库中缺少新闻 ID：{missing}")
    return [records_by_id[news_id] for news_id in unique_ids]


def build_article_input(record: NewsRawRecord) -> ObjectiveArticleInput:
    basic = database_article_input(record)
    tags = record.source_tags if isinstance(record.source_tags, list) else []
    return ObjectiveArticleInput(
        news_id=record.id,
        source={"code": record.source.code, "name": record.source.name},
        title=record.title,
        summary=record.summary or "",
        stored_body=basic.stored_body,
        published_at=record.published_at.isoformat(),
        source_category=record.source_category or "",
        source_tags=[str(tag) for tag in tags if isinstance(tag, str)],
        source_author=record.source_author or "",
    )


def build_request_payload(article: ObjectiveArticleInput, model: str) -> tuple[dict, str]:
    user_prompt = USER_PROMPT_TEMPLATE + json.dumps(
        {"news": asdict(article)}, ensure_ascii=False, separators=(",", ":")
    )
    return (
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            **GENERATION_PARAMETERS,
        },
        user_prompt,
    )


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _evidence_fields(article: ObjectiveArticleInput) -> Iterable[tuple[str, str]]:
    yield "title", article.title
    yield "summary", article.summary
    yield "stored_body", article.stored_body


def match_evidence(evidence_text: object, article: ObjectiveArticleInput) -> dict:
    if not isinstance(evidence_text, str) or not evidence_text.strip():
        return {
            "matched": False,
            "matched_field": None,
            "match_type": "unmatched",
            "normalized_evidence": None,
            "reason": "evidence_text 不是非空字符串。",
        }
    for field, value in _evidence_fields(article):
        if evidence_text in value:
            return {
                "matched": True,
                "matched_field": field,
                "match_type": "exact",
                "normalized_evidence": _normalize_whitespace(evidence_text),
                "reason": None,
            }
    normalized = _normalize_whitespace(evidence_text)
    for field, value in _evidence_fields(article):
        if normalized in _normalize_whitespace(value):
            return {
                "matched": True,
                "matched_field": field,
                "match_type": "whitespace_normalized",
                "normalized_evidence": normalized,
                "reason": None,
            }
    return {
        "matched": False,
        "matched_field": None,
        "match_type": "unmatched",
        "normalized_evidence": normalized,
        "reason": "统一空白字符后，未在 title、summary 或 stored_body 的单一字段中找到连续匹配。",
    }


def _valid_time_atom(value: str) -> bool:
    if re.fullmatch(r"\d{4}", value):
        return True
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", value)
    if quarter:
        return True
    month = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if month:
        try:
            date(int(month.group(1)), int(month.group(2)), 1)
            return True
        except ValueError:
            return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.+)?", value))
    except ValueError:
        return False


def valid_time_value(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parts = value.split("/")
    return len(parts) <= 2 and all(_valid_time_atom(part) for part in parts)


def _month_name(month: int) -> str:
    return (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )[month - 1]


def time_supported_by_evidence(time_value: str, evidence_text: str) -> bool:
    normalized = _normalize_whitespace(evidence_text)
    if time_value in normalized:
        return True
    if "/" in time_value:
        return all(time_supported_by_evidence(part, evidence_text) for part in time_value.split("/"))
    quarter = re.fullmatch(r"(\d{4})-Q([1-4])", time_value)
    if quarter:
        year, number = quarter.groups()
        return bool(
            re.search(rf"\bQ{number}(?:\s+of)?\s+{year}\b", normalized, re.IGNORECASE)
        )
    month = re.fullmatch(r"(\d{4})-(\d{2})", time_value)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        name = _month_name(number)
        return bool(re.search(rf"\b{name}\s+{year}\b", normalized, re.IGNORECASE))
    date_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}).*)?$", time_value)
    if date_match:
        year, month_number, day, hour, minute = date_match.groups()
        name = _month_name(int(month_number))
        date_supported = bool(
            re.search(
                rf"\b(?:{name}\s+{int(day)},\s*{year}|{int(day)}\s+{name}\s+{year})\b|{year}年\s*{int(month_number)}月\s*{int(day)}日",
                normalized,
                re.IGNORECASE,
            )
        )
        if not date_supported:
            return False
        if hour is not None:
            return f"{hour}:{minute}" in normalized
        return True
    return False


def _issue(code: str, message: str, *, path: str | None = None) -> dict:
    issue = {"code": code, "message": message}
    if path is not None:
        issue["path"] = path
    return issue


def deduplicate_facts(parsed: object) -> tuple[object, list[dict]]:
    """Remove only exact normalized statement duplicates and report every removal."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("facts"), list):
        return parsed, []
    normalized = deepcopy(parsed)
    unique_facts: list[object] = []
    seen_statements: dict[str, int] = {}
    warnings: list[dict] = []
    for original_index, fact in enumerate(normalized["facts"]):
        statement = fact.get("statement") if isinstance(fact, dict) else None
        key = (
            _normalize_whitespace(statement).casefold()
            if isinstance(statement, str) and statement.strip()
            else ""
        )
        if key and key in seen_statements:
            warnings.append(
                _issue(
                    "DUPLICATE_FACT_REMOVED",
                    (
                        "模型重复输出了相同 statement；正式 facts 保留首次出现项，"
                        "被移除项仍可在 AI 原始返回中追踪。"
                    ),
                    path=f"facts[{original_index}]",
                )
            )
            continue
        if key:
            seen_statements[key] = original_index
        unique_facts.append(fact)
    normalized["facts"] = unique_facts
    return normalized, warnings


def validate_parsed_result(parsed: object, article: ObjectiveArticleInput) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    evidence_matches: list[dict] = []
    if not isinstance(parsed, dict):
        errors.append(_issue("RESULT_NOT_OBJECT", "顶层结果必须是 JSON 对象。"))
        return {"errors": errors, "warnings": warnings, "evidence_matches": evidence_matches}

    missing = sorted(REQUIRED_RESULT_FIELDS - set(parsed))
    extra = sorted(set(parsed) - REQUIRED_RESULT_FIELDS)
    if missing:
        errors.append(_issue("MISSING_FIELDS", f"缺少必填字段：{missing}"))
    if extra:
        errors.append(_issue("EXTRA_FIELDS", f"包含额外字段：{extra}"))

    for field in ("event_title", "event_time", "action", "objective_summary"):
        value = parsed.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(_issue("INVALID_FIELD_TYPE", f"{field} 必须是非空字符串或 null。", path=field))
    for field in ("actors", "object"):
        value = parsed.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(_issue("INVALID_FIELD_TYPE", f"{field} 必须是非空字符串数组。", path=field))

    status = parsed.get("event_status")
    if status not in EVENT_STATUSES:
        errors.append(_issue("INVALID_EVENT_STATUS", "event_status 枚举非法。", path="event_status"))
    completeness = parsed.get("information_completeness")
    if completeness not in INFORMATION_COMPLETENESS:
        errors.append(
            _issue("INVALID_INFORMATION_COMPLETENESS", "information_completeness 枚举非法。", path="information_completeness")
        )
    event_time = parsed.get("event_time")
    if event_time is not None and not valid_time_value(event_time):
        errors.append(_issue("INVALID_EVENT_TIME", "event_time 格式非法。", path="event_time"))

    facts = parsed.get("facts")
    if not isinstance(facts, list):
        errors.append(_issue("INVALID_FACTS_TYPE", "facts 必须是数组。", path="facts"))
        facts = []
    valid_fact_times: list[str] = []
    for index, fact in enumerate(facts):
        path = f"facts[{index}]"
        if not isinstance(fact, dict):
            errors.append(_issue("FACT_NOT_OBJECT", "事实条目必须是对象。", path=path))
            continue
        fact_missing = sorted(REQUIRED_FACT_FIELDS - set(fact))
        fact_extra = sorted(set(fact) - REQUIRED_FACT_FIELDS)
        if fact_missing:
            errors.append(_issue("FACT_MISSING_FIELDS", f"事实缺少字段：{fact_missing}", path=path))
        if fact_extra:
            errors.append(_issue("FACT_EXTRA_FIELDS", f"事实包含额外字段：{fact_extra}", path=path))
        statement = fact.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            errors.append(_issue("INVALID_STATEMENT", "statement 必须是非空字符串。", path=f"{path}.statement"))
        claim_type = fact.get("claim_type")
        if claim_type not in CLAIM_TYPES:
            errors.append(_issue("INVALID_CLAIM_TYPE", "claim_type 枚举非法。", path=f"{path}.claim_type"))
        evidence = fact.get("evidence_text")
        match = match_evidence(evidence, article)
        evidence_matches.append({"fact_index": index, **match})
        if not match["matched"]:
            errors.append(_issue("EVIDENCE_NOT_MATCHED", match["reason"], path=f"{path}.evidence_text"))
        fact_time = fact.get("fact_time")
        if fact_time is not None:
            if not valid_time_value(fact_time):
                errors.append(_issue("INVALID_FACT_TIME", "fact_time 格式非法。", path=f"{path}.fact_time"))
            elif not isinstance(evidence, str) or not time_supported_by_evidence(fact_time, evidence):
                errors.append(
                    _issue("FACT_TIME_NOT_IN_EVIDENCE", "fact_time 没有被该事实的 evidence_text 明确支持。", path=f"{path}.fact_time")
                )
            else:
                valid_fact_times.append(fact_time)
        amounts = fact.get("amounts")
        if not isinstance(amounts, list):
            errors.append(_issue("INVALID_AMOUNTS_TYPE", "amounts 必须是数组。", path=f"{path}.amounts"))
            continue
        for amount_index, amount in enumerate(amounts):
            amount_path = f"{path}.amounts[{amount_index}]"
            if not isinstance(amount, dict) or set(amount) != REQUIRED_AMOUNT_FIELDS:
                errors.append(_issue("INVALID_AMOUNT_STRUCTURE", "amount 必须只含 text 和 kind。", path=amount_path))
                continue
            text = amount.get("text")
            kind = amount.get("kind")
            if not isinstance(text, str) or not text.strip() or not NUMBER_PATTERN.search(text):
                errors.append(_issue("INVALID_AMOUNT_TEXT", "amount.text 必须是包含数字的非空原文片段。", path=f"{amount_path}.text"))
            elif not isinstance(evidence, str) or _normalize_whitespace(text) not in _normalize_whitespace(evidence):
                errors.append(_issue("AMOUNT_NOT_IN_EVIDENCE", "amount.text 未逐字出现在 evidence_text 中。", path=f"{amount_path}.text"))
            if kind not in AMOUNT_KINDS:
                errors.append(_issue("INVALID_AMOUNT_KIND", "amount.kind 枚举非法。", path=f"{amount_path}.kind"))

    if event_time is not None and event_time not in valid_fact_times:
        errors.append(_issue("EVENT_TIME_NOT_BACKED_BY_FACT", "event_time 必须等于至少一个有证据支持的 fact_time。", path="event_time"))

    if completeness == "insufficient" and facts:
        warnings.append(
            _issue(
                "LIMITED_SOURCE_CONTEXT",
                "输入信息不完整，但仍提取出了有原文证据支持的事实。",
            )
        )
    elif completeness in {"sufficient", "partial"} and not facts:
        warnings.append(_issue("NO_FACTS_WITH_NON_INSUFFICIENT", "非 insufficient 结果没有 facts。"))
    if not facts and parsed.get("objective_summary") is not None:
        errors.append(_issue("SUMMARY_WITHOUT_FACTS", "facts 为空时 objective_summary 必须为 null。", path="objective_summary"))

    source_text = "\n".join(
        value
        for _, value in _evidence_fields(article)
    )
    output_text = json.dumps(parsed, ensure_ascii=False)
    if not ETH_PATTERN.search(source_text) and ETH_PATTERN.search(output_text):
        errors.append(_issue("UNSUPPORTED_ETH_REFERENCE", "输出出现了输入正文中不存在的 ETH 或 Ethereum。"))

    return {"errors": errors, "warnings": warnings, "evidence_matches": evidence_matches}


class DeepSeekObjectiveFactClient:
    def __init__(self, *, http_client: httpx.Client | None = None):
        self.base_url = settings.NEWS_AI_BASE_URL.rstrip("/")
        self.model = settings.NEWS_AI_MODEL
        self.api_key = settings.NEWS_AI_API_KEY
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=settings.NEWS_AI_TIMEOUT_SECONDS
        )

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def process(self, article: ObjectiveArticleInput) -> dict:
        payload, user_prompt = build_request_payload(article, self.model)
        base = {
            "news_id": article.news_id,
            "database_input": asdict(article),
            "request": {
                "provider": "DeepSeek",
                "endpoint": f"{self.base_url}/chat/completions",
                "model": self.model,
                "generation_parameters": GENERATION_PARAMETERS,
                "system_prompt": SYSTEM_PROMPT,
                "user_prompt": user_prompt,
            },
            "api_response": None,
            "raw_model_output": None,
            "parsed_result": None,
            "json_parse_error": None,
            "validation": {"errors": [], "warnings": [], "evidence_matches": []},
            "processing_status": None,
        }
        if not self.api_key:
            base["processing_status"] = "ai_call_failed"
            base["validation"]["errors"].append(
                _issue("AI_NOT_CONFIGURED", "DeepSeek API 未配置。")
            )
            return base
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
            base["processing_status"] = "ai_call_failed"
            base["validation"]["errors"].append(
                _issue("AI_TRANSPORT_ERROR", type(exc).__name__)
            )
            return base

        try:
            response_payload = response.json()
        except ValueError:
            response_payload = {
                "http_status": response.status_code,
                "non_json_body": response.text[:20_000],
            }
        base["api_response"] = response_payload
        if response.status_code >= 400:
            base["processing_status"] = "ai_call_failed"
            base["validation"]["errors"].append(
                _issue("AI_HTTP_ERROR", f"DeepSeek 返回 HTTP {response.status_code}。")
            )
            return base
        try:
            choices = response_payload.get("choices")
            choice = choices[0]
            message = choice["message"]
            raw_output = message["content"]
            if not isinstance(raw_output, str):
                raise TypeError
        except (AttributeError, IndexError, KeyError, TypeError):
            base["processing_status"] = "ai_call_failed"
            base["validation"]["errors"].append(
                _issue("AI_RESPONSE_STRUCTURE_ERROR", "DeepSeek API 响应缺少可用的 message.content。")
            )
            return base
        base["raw_model_output"] = raw_output
        try:
            raw_parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            base["processing_status"] = "json_parse_failed"
            base["json_parse_error"] = f"{exc.msg}（第 {exc.lineno} 行，第 {exc.colno} 列）"
            base["validation"]["errors"].append(
                _issue("MODEL_OUTPUT_JSON_ERROR", f"模型输出 JSON 无法解析：{exc.msg}。")
            )
            return base
        parsed, deduplication_warnings = deduplicate_facts(raw_parsed)
        base["parsed_result"] = parsed
        base["validation"] = validate_parsed_result(parsed, article)
        base["validation"]["warnings"].extend(deduplication_warnings)
        base["processing_status"] = (
            "validation_failed" if base["validation"]["errors"] else "success"
        )
        return base


def _report_statistics(results: list[dict]) -> dict:
    completeness = Counter()
    claim_types = Counter()
    fact_count = 0
    evidence_success = 0
    evidence_failure = 0
    for item in results:
        parsed = item.get("parsed_result")
        if isinstance(parsed, dict):
            value = parsed.get("information_completeness")
            if value in INFORMATION_COMPLETENESS:
                completeness[value] += 1
            facts = parsed.get("facts")
            if isinstance(facts, list):
                fact_count += len(facts)
                for fact in facts:
                    if isinstance(fact, dict) and fact.get("claim_type") in CLAIM_TYPES:
                        claim_types[fact["claim_type"]] += 1
        for match in item["validation"]["evidence_matches"]:
            if match["matched"]:
                evidence_success += 1
            else:
                evidence_failure += 1
    statuses = Counter(item["processing_status"] for item in results)
    return {
        "requested_news_count": len(results),
        "successful_extraction_count": statuses.get("success", 0),
        "information_completeness_counts": {
            value: completeness.get(value, 0)
            for value in ("sufficient", "partial", "insufficient")
        },
        "fact_count": fact_count,
        "claim_type_counts": {
            value: claim_types.get(value, 0) for value in sorted(CLAIM_TYPES)
        },
        "evidence_match_success_count": evidence_success,
        "evidence_match_failure_count": evidence_failure,
        "json_parse_failure_count": statuses.get("json_parse_failed", 0),
        "structure_or_content_validation_failure_count": statuses.get("validation_failed", 0),
        "ai_call_failure_count": statuses.get("ai_call_failed", 0),
        "warning_count": sum(len(item["validation"]["warnings"]) for item in results),
        "validation_error_count": sum(len(item["validation"]["errors"]) for item in results),
    }


def run_objective_fact_validation(news_ids: list[int]) -> dict:
    audit = previous_run_audit()
    before = business_state_snapshot()
    records = select_records(news_ids)
    articles = [build_article_input(record) for record in records]
    client = DeepSeekObjectiveFactClient()
    try:
        results = [client.process(article) for article in articles]
    finally:
        client.close()
    after = business_state_snapshot()
    return {
        "report_kind": "temporary_single_article_deepseek_objective_fact_validation",
        "generated_at": datetime.now().astimezone().isoformat(),
        "prompt_version": PROMPT_VERSION,
        "previous_run_model_audit": audit,
        "provider": "DeepSeek",
        "provider_host": urlsplit(settings.NEWS_AI_BASE_URL).hostname,
        "configured_model": settings.NEWS_AI_MODEL,
        "actual_models_returned_by_api": sorted(
            {
                result["api_response"].get("model")
                for result in results
                if isinstance(result.get("api_response"), dict)
                and isinstance(result["api_response"].get("model"), str)
            }
        ),
        "generation_parameters": GENERATION_PARAMETERS,
        "complete_prompt": {
            "system": SYSTEM_PROMPT,
            "user_template": USER_PROMPT_TEMPLATE,
            "actual_user_prompts_by_news_id": {
                str(result["news_id"]): result["request"]["user_prompt"]
                for result in results
            },
        },
        "execution": {
            "one_request_per_news": True,
            "automatic_retry_count": 0,
            "event_merging": False,
            "network_article_loading": False,
            "database_write": False,
        },
        "statistics": _report_statistics(results),
        "business_data_verification": {
            "before": before,
            "after": after,
            "unchanged": before == after,
        },
        "items": results,
    }


def write_report(report: dict, output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
