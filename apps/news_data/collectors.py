from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import sleep
from typing import Callable
from xml.etree import ElementTree

import httpx
from django.utils import timezone


class NewsCollectionError(RuntimeError):
    """Safe, user-displayable collection failure."""

    def __init__(self, message: str, *, code: str = "request_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchResult:
    response: httpx.Response
    started_at: datetime
    finished_at: datetime
    request_count: int
    retry_count: int

    @property
    def response_hash(self) -> str:
        return hashlib.sha256(self.response.content).hexdigest()


@dataclass(frozen=True, slots=True)
class ParsedNewsItem:
    source_item_id: str
    original_url: str
    title: str
    summary: str
    published_at: datetime
    updated_at_source: datetime | None
    language: str
    source_category: str
    source_tags: list[str]
    raw_payload: dict[str, object]


def parse_source_datetime(value: object) -> datetime:
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit()
    ):
        numeric = int(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=UTC)
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing publication time")
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class NewsRequestClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "Market-Evidence-Lab/1.0 (+read-only collector)"},
        )
        self.last_started_at: datetime | None = None
        self.last_finished_at: datetime | None = None
        self.last_request_count = 0
        self.last_retry_count = 0
        self.last_response: httpx.Response | None = None

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def get(self, url: str, *, params: dict[str, object] | None = None) -> FetchResult:
        self.last_started_at = timezone.now()
        self.last_finished_at = None
        self.last_request_count = 0
        self.last_retry_count = 0
        self.last_response = None
        for attempt in range(self.max_retries + 1):
            self.last_request_count += 1
            try:
                response = self.http_client.get(url, params=params)
                self.last_response = response
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    self.last_retry_count += 1
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
                self.last_finished_at = timezone.now()
                raise NewsCollectionError(
                    f"来源网络请求失败：{exc.__class__.__name__}",
                    code="network_error",
                ) from exc
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    self.last_retry_count += 1
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
            self.last_finished_at = timezone.now()
            if response.status_code >= 400:
                raise NewsCollectionError(
                    f"来源请求返回 HTTP {response.status_code}，响应正文未保存。",
                    code="http_error",
                )
            return FetchResult(
                response=response,
                started_at=self.last_started_at,
                finished_at=self.last_finished_at,
                request_count=self.last_request_count,
                retry_count=self.last_retry_count,
            )
        raise NewsCollectionError("来源请求在有限重试后失败。")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in element:
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _rss_raw_payload(item) -> dict[str, object]:
    payload: dict[str, object] = {}
    for child in item:
        key = _local_name(child.tag)
        value: object = "".join(child.itertext()).strip()
        if child.attrib:
            value = {"text": value, "attributes": dict(child.attrib)}
        if key in payload:
            current = payload[key]
            payload[key] = current + [value] if isinstance(current, list) else [current, value]
        else:
            payload[key] = value
    return payload


def parse_ethereum_feed(content: bytes) -> tuple[list[ParsedNewsItem], int]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise NewsCollectionError("Ethereum Foundation Feed XML 无法解析。", code="invalid_xml") from exc

    root_name = _local_name(root.tag)
    if root_name == "rss":
        containers = [child for child in root if _local_name(child.tag) == "channel"]
        if not containers:
            raise NewsCollectionError("Ethereum Foundation RSS 缺少 channel。", code="unknown_feed")
        entries = [child for child in containers[0] if _local_name(child.tag) == "item"]
        mode = "rss"
    elif root_name == "feed":
        entries = [child for child in root if _local_name(child.tag) == "entry"]
        mode = "atom"
    else:
        raise NewsCollectionError("Ethereum Foundation Feed 格式无法识别。", code="unknown_feed")

    parsed: list[ParsedNewsItem] = []
    invalid = 0
    for entry in entries:
        try:
            title = _child_text(entry, "title")
            if mode == "rss":
                link = _child_text(entry, "link")
                source_item_id = _child_text(entry, "guid")
                published_text = _child_text(entry, "pubdate", "published")
                updated_text = _child_text(entry, "updated")
                summary = _child_text(entry, "description", "summary")
            else:
                link_element = next(
                    (
                        child
                        for child in entry
                        if _local_name(child.tag) == "link"
                        and child.attrib.get("rel", "alternate") == "alternate"
                    ),
                    None,
                )
                link = "" if link_element is None else link_element.attrib.get("href", "")
                source_item_id = _child_text(entry, "id", "guid")
                published_text = _child_text(entry, "published", "updated")
                updated_text = _child_text(entry, "updated")
                summary = _child_text(entry, "summary", "content")
            if not title or not link:
                raise ValueError("missing title or link")
            categories = []
            for child in entry:
                if _local_name(child.tag) != "category":
                    continue
                category = child.attrib.get("term") or "".join(child.itertext()).strip()
                if category:
                    categories.append(category)
            published_at = parse_source_datetime(published_text)
            parsed.append(
                ParsedNewsItem(
                    source_item_id=source_item_id,
                    original_url=link,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    updated_at_source=(
                        parse_source_datetime(updated_text) if updated_text else None
                    ),
                    language="en",
                    source_category=categories[0] if categories else "",
                    source_tags=categories,
                    raw_payload=_rss_raw_payload(entry),
                )
            )
        except (TypeError, ValueError, OverflowError):
            invalid += 1
    return parsed, invalid


CHALLENGE_MARKERS = (
    "captcha",
    "cf-chl-",
    "challenge-platform",
    "verify you are human",
    "access denied",
)


def parse_binance_page(content: bytes) -> list[dict[str, object]]:
    text = content.decode("utf-8", errors="replace")
    lowered = text[:20_000].lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise NewsCollectionError("Binance 返回挑战或访问限制页面。", code="challenge_page")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        if "login" in lowered or "sign in" in lowered:
            code = "login_page"
        elif "not found" in lowered or "404" in lowered:
            code = "soft_404"
        else:
            code = "invalid_json"
        raise NewsCollectionError("Binance 公告列表未返回预期 JSON。", code=code) from exc
    if not isinstance(payload, dict) or payload.get("code") != "000000":
        raise NewsCollectionError("Binance 公告接口返回失败状态。", code="api_error")
    data = payload.get("data")
    catalogs = data.get("catalogs") if isinstance(data, dict) else None
    if not isinstance(catalogs, list):
        raise NewsCollectionError("Binance 公告列表结构发生变化。", code="schema_changed")
    for catalog in catalogs:
        if (
            not isinstance(catalog, dict)
            or not catalog.get("catalogId")
            or not isinstance(catalog.get("articles"), list)
        ):
            raise NewsCollectionError(
                "Binance 公告分类结构发生变化。", code="schema_changed"
            )
        try:
            int(catalog.get("total") or 0)
        except (TypeError, ValueError) as exc:
            raise NewsCollectionError(
                "Binance 公告分类总数格式发生变化。", code="schema_changed"
            ) from exc
    return catalogs


def parse_binance_articles(
    catalogs: list[dict[str, object]], *, base_url: str, article_path: str
) -> tuple[list[ParsedNewsItem], int, dict[str, dict[str, int | bool]]]:
    parsed: list[ParsedNewsItem] = []
    invalid = 0
    catalog_state: dict[str, dict[str, int | bool]] = {}
    for catalog in catalogs:
        if not isinstance(catalog, dict):
            invalid += 1
            continue
        catalog_id = str(catalog.get("catalogId") or "")
        category = str(catalog.get("catalogName") or "")
        articles = catalog.get("articles")
        try:
            total = max(int(catalog.get("total") or 0), 0)
        except (TypeError, ValueError):
            total = 0
        if not catalog_id or not isinstance(articles, list):
            invalid += 1
            continue
        catalog_state[catalog_id] = {"total": total, "valid": 0}
        for article in articles:
            if not isinstance(article, dict):
                invalid += 1
                continue
            try:
                code = str(article.get("code") or "").strip()
                item_id = code or str(article.get("id") or "").strip()
                title = str(article.get("title") or "").strip()
                published_at = parse_source_datetime(article.get("releaseDate"))
                if not item_id or not code or not title:
                    raise ValueError("missing required article field")
                parsed.append(
                    ParsedNewsItem(
                        source_item_id=item_id,
                        original_url=f"{base_url}{article_path.format(code=code)}",
                        title=title,
                        summary="",
                        published_at=published_at,
                        updated_at_source=None,
                        language="en",
                        source_category=category,
                        source_tags=[category] if category else [],
                        raw_payload={
                            "catalogId": catalog.get("catalogId"),
                            "catalogName": catalog.get("catalogName"),
                            "article": article,
                        },
                    )
                )
                catalog_state[catalog_id]["valid"] = int(catalog_state[catalog_id]["valid"]) + 1
            except (TypeError, ValueError, OverflowError):
                invalid += 1
    return parsed, invalid, catalog_state
