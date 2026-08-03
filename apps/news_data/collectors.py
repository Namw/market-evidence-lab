from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from time import monotonic, sleep
from typing import Callable
from urllib.parse import urljoin, urlsplit
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
    source_author: str = ""
    occurred_at: datetime | None = None
    canonical_url_supported: bool = True


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
    _rate_limit_lock = threading.Lock()
    _last_request_by_key: dict[str, float] = {}

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = sleep,
        user_agent: str = "MarketEvidenceLab/1.0 jackywangcode@gmail.com",
        rate_limit_key: str = "",
        min_request_interval_seconds: float = 0,
    ) -> None:
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self.rate_limit_key = rate_limit_key
        self.min_request_interval_seconds = max(0, min_request_interval_seconds)
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
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
            self._wait_for_rate_limit()
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

    def _wait_for_rate_limit(self) -> None:
        if not self.rate_limit_key or not self.min_request_interval_seconds:
            return
        with self._rate_limit_lock:
            now = monotonic()
            previous = self._last_request_by_key.get(self.rate_limit_key)
            if previous is not None:
                remaining = self.min_request_interval_seconds - (now - previous)
                if remaining > 0:
                    self.sleep_fn(remaining)
                    now = monotonic()
            self._last_request_by_key[self.rate_limit_key] = now


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


_BARE_AMPERSAND = re.compile(
    r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]+;)"
)
_INVALID_XML_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _recover_xml(content: bytes) -> tuple[ElementTree.Element, bool]:
    try:
        return ElementTree.fromstring(content), False
    except ElementTree.ParseError as first_error:
        text = content.decode("utf-8", errors="replace")
        repaired = _INVALID_XML_CONTROL.sub("", _BARE_AMPERSAND.sub("&amp;", text))
        try:
            return ElementTree.fromstring(repaired.encode("utf-8")), True
        except ElementTree.ParseError as exc:
            raise NewsCollectionError(
                "RSS / Atom XML 无法在有限容错后解析。", code="invalid_xml"
            ) from first_error


def parse_rss_feed(
    content: bytes, *, feed_category: str = ""
) -> tuple[list[ParsedNewsItem], int, bool]:
    root, recovered = _recover_xml(content)

    root_name = _local_name(root.tag)
    if root_name == "rss":
        containers = [child for child in root if _local_name(child.tag) == "channel"]
        if not containers:
            raise NewsCollectionError("RSS 缺少 channel。", code="unknown_feed")
        entries = [child for child in containers[0] if _local_name(child.tag) == "item"]
        mode = "rss"
    elif root_name == "feed":
        entries = [child for child in root if _local_name(child.tag) == "entry"]
        mode = "atom"
    else:
        raise NewsCollectionError("RSS / Atom Feed 格式无法识别。", code="unknown_feed")

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
            source_author = _child_text(entry, "creator", "author")
            categories = []
            for child in entry:
                if _local_name(child.tag) != "category":
                    continue
                category = child.attrib.get("term") or "".join(child.itertext()).strip()
                if category:
                    categories.append(category)
            published_at = parse_source_datetime(published_text)
            tags = list(dict.fromkeys([*categories, feed_category] if feed_category else categories))
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
                    source_category=categories[0] if categories else feed_category,
                    source_tags=tags,
                    raw_payload=_rss_raw_payload(entry),
                    source_author=source_author,
                )
            )
        except (TypeError, ValueError, OverflowError):
            invalid += 1
    return parsed, invalid, recovered


def parse_ethereum_feed(content: bytes) -> tuple[list[ParsedNewsItem], int]:
    parsed, invalid, _ = parse_rss_feed(content)
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


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def _html_text(value: object) -> str:
    parser = _TextOnlyHTMLParser()
    parser.feed(str(value or ""))
    parser.close()
    return parser.text()


def parse_tether_page(content: bytes) -> tuple[list[ParsedNewsItem], int]:
    text = content.decode("utf-8", errors="replace")
    lowered = text[:20_000].lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise NewsCollectionError("Tether 返回挑战或访问限制页面。", code="challenge_page")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NewsCollectionError("Tether 新闻接口未返回预期 JSON。", code="invalid_json") from exc
    if not isinstance(payload, list):
        raise NewsCollectionError("Tether 新闻接口结构发生变化。", code="schema_changed")

    parsed: list[ParsedNewsItem] = []
    invalid = 0
    for post in payload:
        try:
            if not isinstance(post, dict):
                raise ValueError("post is not an object")
            item_id = str(post.get("id") or "").strip()
            link = str(post.get("link") or "").strip()
            title_payload = post.get("title")
            excerpt_payload = post.get("excerpt")
            if not isinstance(title_payload, dict) or not isinstance(excerpt_payload, dict):
                raise ValueError("missing rendered fields")
            title = _html_text(title_payload.get("rendered"))
            summary = _html_text(excerpt_payload.get("rendered"))
            published_at = parse_source_datetime(post.get("date_gmt"))
            modified = post.get("modified_gmt")
            if not item_id or not link or not title:
                raise ValueError("missing required post field")

            category_names: list[str] = []
            tag_names: list[str] = []
            embedded = post.get("_embedded")
            term_groups = embedded.get("wp:term") if isinstance(embedded, dict) else []
            if isinstance(term_groups, list):
                for group in term_groups:
                    if not isinstance(group, list):
                        continue
                    for term in group:
                        if not isinstance(term, dict):
                            continue
                        name = _html_text(term.get("name"))
                        if not name:
                            continue
                        if term.get("taxonomy") == "post_tag":
                            tag_names.append(name)
                        elif term.get("taxonomy") == "category":
                            category_names.append(name)

            source_author = ""
            authors = embedded.get("author") if isinstance(embedded, dict) else []
            if isinstance(authors, list):
                source_author = next(
                    (
                        _html_text(author.get("name"))
                        for author in authors
                        if isinstance(author, dict) and author.get("name")
                    ),
                    "",
                )
            categories = list(dict.fromkeys(category_names))
            tags = list(dict.fromkeys([*categories, *tag_names]))
            primary_category = next(
                (category for category in categories if category.casefold() != "news"),
                categories[0] if categories else "News",
            )
            parsed.append(
                ParsedNewsItem(
                    source_item_id=item_id,
                    original_url=link,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    updated_at_source=(
                        parse_source_datetime(modified) if modified else None
                    ),
                    language="en",
                    source_category=primary_category,
                    source_tags=tags,
                    source_author=source_author,
                    raw_payload={
                        "id": post.get("id"),
                        "date_gmt": post.get("date_gmt"),
                        "modified_gmt": post.get("modified_gmt"),
                        "slug": post.get("slug"),
                        "status": post.get("status"),
                        "type": post.get("type"),
                        "link": post.get("link"),
                        "title": title_payload,
                        "excerpt": excerpt_payload,
                        "author": post.get("author"),
                        "categories": post.get("categories"),
                        "tags": post.get("tags"),
                        "reading_time": post.get("reading_time"),
                    },
                )
            )
        except (TypeError, ValueError, OverflowError):
            invalid += 1
    return parsed, invalid


class _SlowMistHackedHTMLParser(HTMLParser):
    """Extract event cards without depending on the rest of the archive layout."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_event_list = False
        self.event_list_depth = 0
        self.current_event: dict[str, str] | None = None
        self.capture_name = ""
        self.capture_tag = ""
        self.capture_parts: list[str] = []
        self.events: list[dict[str, str]] = []
        self.document_parts: list[str] = []
        self.total_pages = 0

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return set(value.split())

    def _start_capture(self, name: str, tag: str) -> None:
        self.capture_name = name
        self.capture_tag = tag
        self.capture_parts = []

    def _finish_capture(self) -> None:
        if self.current_event is None or not self.capture_name:
            return
        value = re.sub(r"\s+", " ", " ".join(self.capture_parts)).strip()
        if self.capture_name == "date":
            self.current_event["date"] = value
        elif self.capture_name == "target":
            self.current_event["target"] = re.sub(
                r"^Hacked target:\s*", "", value, flags=re.IGNORECASE
            ).strip()
        elif self.capture_name == "paragraph":
            description = re.match(
                r"^Description of the event:\s*(.*)$", value, flags=re.IGNORECASE
            )
            loss_and_method = re.match(
                r"^Amount of loss:\s*(.*?)\s*Attack method:\s*(.*)$",
                value,
                flags=re.IGNORECASE,
            )
            if description:
                self.current_event["description"] = description.group(1).strip()
            elif loss_and_method:
                self.current_event["loss"] = loss_and_method.group(1).strip()
                self.current_event["attack_method"] = loss_and_method.group(2).strip()
        self.capture_name = ""
        self.capture_tag = ""
        self.capture_parts = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attrs_dict = dict(attrs)
        if tag == "a":
            href = (attrs_dict.get("href") or "").strip()
            page_match = re.search(r"[?&]page=(\d+)", href)
            if page_match:
                self.total_pages = max(self.total_pages, int(page_match.group(1)))

        if tag == "div":
            classes = self._classes(attrs)
            if not self.in_event_list and "case-content" in classes:
                self.in_event_list = True
                self.event_list_depth = 1
                return
            if self.in_event_list:
                self.event_list_depth += 1

        if not self.in_event_list:
            return
        if tag == "li" and self.current_event is None:
            self.current_event = {}
            return
        if self.current_event is None:
            return
        if tag == "span" and "time" in self._classes(attrs):
            self._start_capture("date", tag)
        elif tag == "h3":
            self._start_capture("target", tag)
        elif tag == "p":
            self._start_capture("paragraph", tag)
        elif tag == "a" and attrs_dict.get("href"):
            self.current_event["reference_url"] = str(attrs_dict["href"]).strip()

    def handle_endtag(self, tag: str) -> None:
        if self.capture_name and tag == self.capture_tag:
            self._finish_capture()
        if self.in_event_list and tag == "li" and self.current_event is not None:
            self.events.append(self.current_event)
            self.current_event = None
        if self.in_event_list and tag == "div":
            self.event_list_depth -= 1
            if self.event_list_depth <= 0:
                self.in_event_list = False
                self.event_list_depth = 0

    def handle_data(self, data: str) -> None:
        self.document_parts.append(data)
        if self.capture_name:
            self.capture_parts.append(data)

    def resolved_total_pages(self, current_page: int) -> int:
        document_text = re.sub(r"\s+", " ", " ".join(self.document_parts))
        page_match = re.search(
            r"Page\s+\d+\s+of\s+(\d+)", document_text, flags=re.IGNORECASE
        )
        if page_match:
            self.total_pages = max(self.total_pages, int(page_match.group(1)))
        return max(self.total_pages, current_page)


def parse_slowmist_hacked_page(
    content: bytes,
    *,
    page_number: int,
) -> tuple[list[ParsedNewsItem], int, int]:
    text = content.decode("utf-8", errors="replace")
    lowered = text[:20_000].lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise NewsCollectionError(
            "SlowMist Hacked 返回挑战或访问限制页面。", code="challenge_page"
        )

    parser = _SlowMistHackedHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise NewsCollectionError(
            "SlowMist Hacked 页面无法解析。", code="invalid_html"
        ) from exc

    parsed: list[ParsedNewsItem] = []
    invalid = 0
    for event in parser.events:
        try:
            event_date = event.get("date", "").strip()
            target = event.get("target", "").strip()
            description = event.get("description", "").strip()
            if not event_date or not target or not description:
                raise ValueError("missing required event field")
            published_at = parse_source_datetime(event_date)
            attack_method = event.get("attack_method", "").strip()
            reference_url = event.get("reference_url", "").strip()
            source_item_id = "slowmist-" + hashlib.sha256(
                f"{event_date}\0{target.casefold()}".encode("utf-8")
            ).hexdigest()
            tags = list(
                dict.fromkeys(
                    value for value in ["Security incident", attack_method] if value
                )
            )
            parsed.append(
                ParsedNewsItem(
                    source_item_id=source_item_id,
                    original_url=reference_url,
                    title=f"Hacked target: {target}",
                    summary=description,
                    published_at=published_at,
                    updated_at_source=None,
                    occurred_at=published_at,
                    language="en",
                    source_category=attack_method or "Security incident",
                    source_tags=tags,
                    source_author="SlowMist",
                    canonical_url_supported=False,
                    raw_payload={
                        "event_date": event_date,
                        "target": target,
                        "description": description,
                        "amount_of_loss": event.get("loss", ""),
                        "attack_method": attack_method,
                        "reference_url": reference_url,
                    },
                )
            )
        except (TypeError, ValueError, OverflowError):
            invalid += 1
    return parsed, invalid, parser.resolved_total_pages(page_number)


class _CirclePressroomHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.list_depth = 0
        self.current_item: dict[str, str] | None = None
        self.capture_name = ""
        self.capture_tag = ""
        self.capture_parts: list[str] = []
        self.items: list[dict[str, str]] = []
        self.next_page_href = ""
        self.page_count_parts: list[str] = []
        self.capture_page_count = False

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        return set((dict(attrs).get("class") or "").split())

    def _start_capture(self, name: str, tag: str) -> None:
        self.capture_name = name
        self.capture_tag = tag
        self.capture_parts = []

    def _finish_capture(self) -> None:
        if self.current_item is not None:
            self.current_item[self.capture_name] = re.sub(
                r"\s+", " ", " ".join(self.capture_parts)
            ).strip()
        self.capture_name = ""
        self.capture_tag = ""
        self.capture_parts = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attrs_dict = dict(attrs)
        classes = self._classes(attrs)
        if tag == "a" and "w-pagination-next" in classes:
            self.next_page_href = (attrs_dict.get("href") or "").strip()
        if tag == "div" and "w-page-count" in classes:
            self.capture_page_count = True
            self.page_count_parts = []

        if tag == "div" and not self.list_depth and attrs_dict.get(
            "fs-list-element"
        ) == "list":
            self.list_depth = 1
            return
        if not self.list_depth:
            return
        if tag == "div":
            self.list_depth += 1
        if (
            tag == "a"
            and self.current_item is None
            and "press-link" in classes
        ):
            self.current_item = {
                "href": (attrs_dict.get("href") or "").strip(),
                "date": "",
                "title": "",
                "description": "",
            }
            return
        if self.current_item is None or tag != "p":
            return
        if attrs_dict.get("fs-list-field") == "title":
            self._start_capture("title", tag)
        elif attrs_dict.get("fs-list-field") == "description":
            self._start_capture("description", tag)
        elif "caption-disclosure" in classes and not self.current_item["date"]:
            self._start_capture("date", tag)

    def handle_endtag(self, tag: str) -> None:
        if self.capture_name and tag == self.capture_tag:
            self._finish_capture()
        if tag == "a" and self.current_item is not None:
            self.items.append(self.current_item)
            self.current_item = None
        if self.capture_page_count and tag == "div":
            self.capture_page_count = False
        if self.list_depth and tag == "div":
            self.list_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.capture_name:
            self.capture_parts.append(data)
        if self.capture_page_count:
            self.page_count_parts.append(data)

    def total_pages(self) -> int:
        text = re.sub(r"\s+", " ", " ".join(self.page_count_parts)).strip()
        match = re.search(r"\b\d+\s*/\s*(\d+)\b", text)
        return max(int(match.group(1)), 1) if match else 1


def _circle_pressroom_url(href: str) -> str:
    url = urljoin("https://www.circle.com/pressroom", href)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme not in {"http", "https"}
        or host not in {"circle.com", "www.circle.com"}
        or not parts.path.startswith("/pressroom/")
    ):
        raise ValueError("invalid Circle pressroom URL")
    return url


def _parse_circle_pressroom_date(value: str) -> datetime:
    text = re.sub(r"\s+", " ", value).strip()
    for date_format in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError("invalid Circle pressroom date")


def parse_circle_pressroom_page(
    content: bytes,
) -> tuple[list[ParsedNewsItem], int, str, int]:
    text = content.decode("utf-8", errors="replace")
    lowered = text[:20_000].lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise NewsCollectionError(
            "Circle Pressroom returned a challenge or access restriction page.",
            code="challenge_page",
        )
    parser = _CirclePressroomHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise NewsCollectionError(
            "Circle Pressroom page could not be parsed.", code="invalid_html"
        ) from exc

    parsed: list[ParsedNewsItem] = []
    invalid = 0
    for entry in parser.items:
        try:
            original_url = _circle_pressroom_url(entry.get("href", ""))
            title = entry.get("title", "").strip()
            summary = entry.get("description", "").strip()
            published_at = _parse_circle_pressroom_date(entry.get("date", ""))
            source_item_id = urlsplit(original_url).path.rstrip("/").rsplit("/", 1)[-1]
            if not source_item_id or not title or not summary:
                raise ValueError("missing required press release field")
            parsed.append(
                ParsedNewsItem(
                    source_item_id=source_item_id,
                    original_url=original_url,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    updated_at_source=None,
                    language="en",
                    source_category="Press Release",
                    source_tags=["Press Release", "Circle"],
                    source_author="Circle",
                    raw_payload={
                        "list_date": entry.get("date", ""),
                        "list_title": title,
                        "list_description": summary,
                        "article_text": "",
                        "article_fetch_status": "not_requested",
                    },
                )
            )
        except (TypeError, ValueError, OverflowError):
            invalid += 1
    next_page_url = ""
    if parser.next_page_href:
        candidate = urljoin(
            "https://www.circle.com/pressroom", parser.next_page_href
        )
        candidate_parts = urlsplit(candidate)
        candidate_host = (candidate_parts.hostname or "").lower().rstrip(".")
        if (
            candidate_parts.scheme in {"http", "https"}
            and candidate_host in {"circle.com", "www.circle.com"}
            and candidate_parts.path.rstrip("/") == "/pressroom"
        ):
            next_page_url = candidate
    return parsed, invalid, next_page_url, parser.total_pages()


class _CircleArticleHTMLParser(HTMLParser):
    block_tags = {"h2", "h3", "p", "li", "blockquote"}
    skip_tags = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body_depth = 0
        self.skip_depth = 0
        self.current_tag = ""
        self.current_parts: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if tag == "div" and not self.body_depth and "press-rich-text" in classes:
            self.body_depth = 1
            return
        if not self.body_depth:
            return
        if tag == "div":
            self.body_depth += 1
        if tag in self.skip_tags:
            self.skip_depth += 1
        if tag in self.block_tags and not self.skip_depth and not self.current_tag:
            self.current_tag = tag
            self.current_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self.current_tag and tag == self.current_tag:
            text = re.sub(r"\s+", " ", " ".join(self.current_parts)).strip()
            if text:
                self.chunks.append(text)
            self.current_tag = ""
            self.current_parts = []
        if self.body_depth and tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
        if self.body_depth and tag == "div":
            self.body_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.body_depth and self.current_tag and not self.skip_depth:
            self.current_parts.append(data)

    def article_text(self) -> str:
        unique: list[str] = []
        seen: set[str] = set()
        for chunk in self.chunks:
            key = chunk.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(chunk)
        return "\n\n".join(unique)[:40_000]


def parse_circle_pressroom_article(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    lowered = text[:20_000].lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise NewsCollectionError(
            "Circle press release returned a challenge or access restriction page.",
            code="challenge_page",
        )
    parser = _CircleArticleHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise NewsCollectionError(
            "Circle press release body could not be parsed.",
            code="article_parse_failed",
        ) from exc
    article_text = parser.article_text()
    if len(article_text) < 40:
        raise NewsCollectionError(
            "Circle press release body could not be parsed.",
            code="article_parse_failed",
        )
    return article_text
