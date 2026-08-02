from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from apps.news_data.models import NewsRawRecord


MAX_RESPONSE_BYTES = 2_000_000
MAX_DISPLAY_CHARS = 40_000
ALLOWED_SOURCE_SUFFIXES = {
    "ethereum_foundation": ("ethereum.org",),
    "binance_announcements": ("binance.com",),
}


class SourceContentError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ArticleContent:
    text: str
    source_url: str


class _ArticleTextParser(HTMLParser):
    block_tags = {"h1", "h2", "h3", "p", "li", "blockquote"}
    skip_tags = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.preferred_depth = 0
        self.skip_depth = 0
        self.current_tag = ""
        self.current_parts: list[str] = []
        self.current_preferred = False
        self.preferred_chunks: list[str] = []
        self.fallback_chunks: list[str] = []
        self.meta_descriptions: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag in {"article", "main"}:
            self.preferred_depth += 1
        if tag in self.skip_tags:
            self.skip_depth += 1
        if tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"}:
                value = attributes.get("content", "").strip()
                if value:
                    self.meta_descriptions.append(value)
        if tag in self.block_tags and not self.skip_depth and not self.current_tag:
            self.current_tag = tag
            self.current_parts = []
            self.current_preferred = bool(self.preferred_depth)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == self.current_tag:
            text = _normalize_text(" ".join(self.current_parts))
            if len(text) >= 20:
                target = self.preferred_chunks if self.current_preferred else self.fallback_chunks
                target.append(text)
            self.current_tag = ""
            self.current_parts = []
        if tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"article", "main"} and self.preferred_depth:
            self.preferred_depth -= 1

    def handle_data(self, data):
        if self.current_tag and not self.skip_depth:
            self.current_parts.append(data)

    def article_text(self) -> str:
        chunks = self.preferred_chunks if len(" ".join(self.preferred_chunks)) >= 120 else self.fallback_chunks
        if not chunks:
            chunks = self.meta_descriptions
        unique: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            normalized = _normalize_text(chunk)
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                unique.append(normalized)
        return "\n\n".join(unique)[:MAX_DISPLAY_CHARS]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _host_allowed(record: NewsRawRecord, url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    host = parts.hostname.lower().rstrip(".")
    suffixes = ALLOWED_SOURCE_SUFFIXES.get(record.source.code, ())
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def extract_article_text(html: str) -> str:
    parser = _ArticleTextParser()
    parser.feed(html)
    parser.close()
    return parser.article_text()


def fetch_source_article(
    record: NewsRawRecord, *, http_client: httpx.Client | None = None
) -> ArticleContent:
    url = record.original_url or record.canonical_url
    if not _host_allowed(record, url):
        raise SourceContentError("新闻来源地址不在允许的官方域名内。")
    owns_client = http_client is None
    client = http_client or httpx.Client(
        timeout=settings.NEWS_ARTICLE_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "MarketEvidenceLab/1.0 (+news reader)"},
    )
    try:
        response = client.get(url)
        response.raise_for_status()
        if not _host_allowed(record, str(response.url)):
            raise SourceContentError("新闻来源重定向到了非官方域名。")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SourceContentError("新闻正文响应过大。")
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not any(
            allowed in content_type for allowed in ("html", "xhtml", "text/plain")
        ):
            raise SourceContentError("新闻来源没有返回可读正文。")
        text = extract_article_text(response.text)
        if len(text) < 40:
            raise SourceContentError("新闻来源正文无法解析。")
        return ArticleContent(text=text, source_url=str(response.url))
    except SourceContentError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceContentError("暂时无法读取新闻来源正文。") from exc
    finally:
        if owns_client:
            client.close()


def summarize_article_text(text: str, limit: int = 600) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    excerpt = normalized[: limit + 1]
    cut = max(excerpt.rfind("。"), excerpt.rfind("！"), excerpt.rfind("？"), excerpt.rfind(". "))
    if cut >= max(120, limit // 2):
        return excerpt[: cut + 1].strip()
    return excerpt[:limit].rstrip() + "…"
