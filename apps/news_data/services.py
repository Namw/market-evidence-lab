from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.collection.models import CollectionRun

from .collectors import (
    FetchResult,
    NewsCollectionError,
    NewsRequestClient,
    ParsedNewsItem,
    parse_binance_articles,
    parse_binance_page,
    parse_circle_pressroom_article,
    parse_circle_pressroom_page,
    parse_ethereum_feed,
    parse_rss_feed,
    parse_slowmist_hacked_page,
    parse_tether_page,
)
from .models import (
    NewsCollectionDiagnostic,
    NewsFeed,
    NewsRawRecord,
    NewsRecordFeed,
    NewsSource,
)
from .sources import (
    BINANCE_ARTICLE_PATH,
    BINANCE_LIST_PARAMS,
    BINANCE_PAGE_SIZE,
    BINANCE_SAFETY_PAGE_LIMIT,
    BINANCE_ANNOUNCEMENTS_CODE,
    CIRCLE_PRESSROOM_CODE,
    CIRCLE_SAFETY_PAGE_LIMIT,
    COLLECTION_OVERLAP_DAYS,
    ETHEREUM_FOUNDATION_CODE,
    FEED_DEFINITIONS,
    SEC_FEED_CODES,
    SLOWMIST_HACKED_CODE,
    SLOWMIST_LIST_PARAMS,
    SLOWMIST_SAFETY_PAGE_LIMIT,
    SOURCE_DEFINITIONS,
    TETHER_LIST_PARAMS,
    TETHER_NEWS_CODE,
    TETHER_SAFETY_PAGE_LIMIT,
    TRACKING_QUERY_PARAMETERS,
)


@dataclass(frozen=True, slots=True)
class SaveCounts:
    inserted: int = 0
    updated: int = 0
    duplicate: int = 0

    def __add__(self, other: "SaveCounts") -> "SaveCounts":
        return SaveCounts(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.duplicate + other.duplicate,
        )


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_QUERY_PARAMETERS
    ]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity_hash(item: ParsedNewsItem, canonical_url: str) -> str:
    if item.source_item_id:
        identity = f"item:{item.source_item_id}"
    elif canonical_url:
        identity = f"url:{canonical_url}"
    else:
        timestamp = item.published_at.isoformat()
        identity = f"fingerprint:{timestamp}:{item.title.strip()}"
    return _sha256(identity)


def _content_hash(item: ParsedNewsItem, canonical_url: str) -> str:
    return _sha256(
        _stable_json(
            {
                "source_item_id": item.source_item_id,
                "canonical_url": canonical_url,
                "title": item.title,
                "summary": item.summary,
                "published_at": item.published_at.isoformat(),
                "updated_at_source": (
                    item.updated_at_source.isoformat() if item.updated_at_source else None
                ),
                "language": item.language,
                "source_category": item.source_category,
                "source_tags": item.source_tags,
                "source_author": item.source_author,
                "raw_payload": item.raw_payload,
            }
        )
    )


@transaction.atomic
def save_news_item(
    *,
    source: NewsSource,
    item: ParsedNewsItem,
    run: CollectionRun,
    seen_at: datetime,
    feed: NewsFeed | None = None,
) -> SaveCounts:
    canonical_url = (
        normalize_url(item.original_url) if item.canonical_url_supported else ""
    )
    identity_hash = _identity_hash(item, canonical_url)
    content_hash = _content_hash(item, canonical_url)
    record = None
    if item.source_item_id:
        record = (
            NewsRawRecord.objects.select_for_update()
            .filter(source=source, source_item_id=item.source_item_id)
            .first()
        )
    if record is None and canonical_url:
        record = (
            NewsRawRecord.objects.select_for_update()
            .filter(source=source, canonical_url=canonical_url)
            .first()
        )
    if record is None:
        record = (
            NewsRawRecord.objects.select_for_update()
            .filter(source=source, identity_hash=identity_hash)
            .first()
        )
    values = {
        "source_item_id": item.source_item_id,
        "original_url": item.original_url,
        "canonical_url": canonical_url,
        "title": item.title,
        "summary": item.summary,
        "published_at": item.published_at,
        "updated_at_source": item.updated_at_source,
        "occurred_at": item.occurred_at,
        "language": item.language,
        "source_category": item.source_category,
        "source_tags": item.source_tags,
        "source_author": item.source_author,
        "identity_hash": identity_hash,
        "content_hash": content_hash,
        "raw_payload": item.raw_payload,
    }
    if record is None:
        record = NewsRawRecord.objects.create(
            source=source,
            **values,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            first_collection_run=run,
            last_collection_run=run,
        )
        if feed is not None:
            NewsRecordFeed.objects.create(
                news_record=record,
                feed=feed,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                first_collection_run=run,
                last_collection_run=run,
            )
        return SaveCounts(inserted=1)

    changed = any(getattr(record, key) != value for key, value in values.items())
    for key, value in values.items():
        setattr(record, key, value)
    record.last_seen_at = seen_at
    record.last_collection_run = run
    update_fields = [*values, "last_seen_at", "last_collection_run", "updated_at"]
    record.save(update_fields=update_fields)
    if feed is not None:
        membership, created = NewsRecordFeed.objects.get_or_create(
            news_record=record,
            feed=feed,
            defaults={
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
                "first_collection_run": run,
                "last_collection_run": run,
            },
        )
        if not created:
            membership.last_seen_at = seen_at
            membership.last_collection_run = run
            membership.save(update_fields=["last_seen_at", "last_collection_run"])
    return SaveCounts(updated=1) if changed else SaveCounts(duplicate=1)


def collection_window(
    source_or_feed: NewsSource | NewsFeed, range_end: datetime
) -> tuple[datetime, datetime]:
    if timezone.is_naive(range_end):
        raise ValueError("range_end must be timezone-aware")
    if source_or_feed.trusted_coverage_end:
        overlap_start = source_or_feed.trusted_coverage_end - timedelta(
            days=COLLECTION_OVERLAP_DAYS
        )
        return max(source_or_feed.activated_at, overlap_start), range_end
    return source_or_feed.activated_at, range_end


def _diagnostic_response_fields(fetch: FetchResult | None, client: NewsRequestClient):
    response = fetch.response if fetch else client.last_response
    content = response.content if response is not None else b""
    return {
        "request_started_at": (
            fetch.started_at if fetch else client.last_started_at or timezone.now()
        ),
        "request_finished_at": (
            fetch.finished_at if fetch else client.last_finished_at or timezone.now()
        ),
        "http_status": response.status_code if response is not None else None,
        "final_url": str(response.url) if response is not None else "",
        "content_type": response.headers.get("content-type", "") if response is not None else "",
        "response_size": len(content),
        "response_hash": hashlib.sha256(content).hexdigest() if content else "",
        "request_count": fetch.request_count if fetch else client.last_request_count,
        "retry_count": fetch.retry_count if fetch else client.last_retry_count,
    }


def _create_diagnostic(
    *,
    run: CollectionRun,
    source: NewsSource,
    feed: NewsFeed | None,
    unit_type: str,
    unit_identifier: str,
    client: NewsRequestClient,
    fetch: FetchResult | None,
    page_number: int | None = None,
    request_count_override: int | None = None,
    retry_count_override: int | None = None,
    **values,
) -> NewsCollectionDiagnostic:
    response_fields = _diagnostic_response_fields(fetch, client)
    if request_count_override is not None:
        response_fields["request_count"] = request_count_override
    if retry_count_override is not None:
        response_fields["retry_count"] = retry_count_override
    return NewsCollectionDiagnostic.objects.create(
        collection_run=run,
        source=source,
        feed=feed,
        unit_type=unit_type,
        unit_identifier=unit_identifier,
        range_start=run.range_start,
        range_end=run.range_end,
        parser_version=feed.parser_version if feed else source.parser_version,
        page_number=page_number,
        **response_fields,
        **values,
    )


def _collect_rss(
    run: CollectionRun,
    source: NewsSource,
    feed: NewsFeed,
    client: NewsRequestClient,
) -> None:
    fetch = None
    try:
        fetch = client.get(feed.feed_url)
        parsed, invalid, recovered = parse_rss_feed(
            fetch.response.content,
            feed_category=feed.name,
        )
        if not parsed:
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.FEED,
                unit_identifier=feed.code,
                client=client,
                fetch=fetch,
                candidate_count=invalid,
                invalid_count=invalid,
                stop_reason=NewsCollectionDiagnostic.StopReason.COMPLETED,
                error_code="zero_parsed_items",
                error_summary="Feed 请求成功，但没有可解析条目。",
            )
            raise NewsCollectionError(
                "Feed 请求成功但解析为 0 条。", code="zero_parsed_items"
            )
        eligible = [
            item
            for item in parsed
            if (
                feed.bootstrap_visible_items
                and feed.trusted_coverage_end is None
                and item.published_at < run.range_end
            )
            or run.range_start <= item.published_at < run.range_end
        ]
        counts = SaveCounts()
        seen_at = timezone.now()
        for item in eligible:
            counts += save_news_item(
                source=source,
                feed=feed,
                item=item,
                run=run,
                seen_at=seen_at,
            )
        published = [item.published_at for item in parsed]
        history_limited = min(published) > run.range_start
        stop_reason = (
            NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            if history_limited
            else NewsCollectionDiagnostic.StopReason.REACHED_TIME_BOUNDARY
        )
        _create_diagnostic(
            run=run,
            source=source,
            feed=feed,
            unit_type=NewsCollectionDiagnostic.UnitType.FEED,
            unit_identifier=feed.code,
            client=client,
            fetch=fetch,
            candidate_count=len(parsed) + invalid,
            parsed_count=len(parsed),
            eligible_count=len(eligible),
            inserted_count=counts.inserted,
            updated_count=counts.updated,
            duplicate_count=counts.duplicate,
            invalid_count=invalid,
            earliest_published_at=min(published),
            latest_published_at=max(published),
            stop_reason=stop_reason,
            coverage_complete=True,
            details={"xml_recovered": recovered},
            error_summary=(
                "Feed XML 含有可恢复的格式问题，已完成有限容错解析。"
                if recovered
                else ""
            ),
        )
    except NewsCollectionError as exc:
        if not NewsCollectionDiagnostic.objects.filter(collection_run=run).exists():
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.FEED,
                unit_identifier=feed.code,
                client=client,
                fetch=fetch,
                stop_reason=NewsCollectionDiagnostic.StopReason.REQUEST_FAILED,
                error_code=exc.code,
                error_summary=str(exc)[:500],
            )
        raise


def _collect_binance(
    run: CollectionRun,
    source: NewsSource,
    feed: NewsFeed,
    client: NewsRequestClient,
    *,
    safety_page_limit: int,
) -> None:
    seen_page_hashes: set[str] = set()
    covered_catalogs: set[str] = set()
    known_catalogs: set[str] = set()
    for page_number in range(1, safety_page_limit + 1):
        fetch = None
        try:
            params = {**BINANCE_LIST_PARAMS, "pageNo": page_number}
            fetch = client.get(feed.feed_url, params=params)
            if "login" in str(fetch.response.url).lower():
                raise NewsCollectionError("Binance 请求被重定向到登录页。", code="login_page")
            catalogs = parse_binance_page(fetch.response.content)
            parsed, invalid, catalog_state = parse_binance_articles(
                catalogs,
                base_url=source.base_url,
                article_path=BINANCE_ARTICLE_PATH,
            )
            if page_number == 1 and not parsed:
                raise NewsCollectionError(
                    "Binance 第一页解析为 0 条，疑似解析器失效。",
                    code="zero_first_page",
                )
            page_identity = _sha256(
                _stable_json(
                    sorted(
                        (item.source_category, item.source_item_id)
                        for item in parsed
                    )
                )
            )
            if page_identity in seen_page_hashes:
                raise NewsCollectionError(
                    "Binance 分页返回重复页面。", code="pagination_loop"
                )
            seen_page_hashes.add(page_identity)
            eligible = [
                item
                for item in parsed
                if run.range_start <= item.published_at < run.range_end
            ]
            counts = SaveCounts()
            seen_at = timezone.now()
            for item in eligible:
                counts += save_news_item(
                    source=source,
                    feed=feed,
                    item=item,
                    run=run,
                    seen_at=seen_at,
                )

            by_catalog: dict[str, list[ParsedNewsItem]] = {}
            for item in parsed:
                by_catalog.setdefault(item.source_category, []).append(item)
            catalog_name_to_id = {
                str(catalog.get("catalogName") or ""): str(catalog.get("catalogId") or "")
                for catalog in catalogs
                if isinstance(catalog, dict)
            }
            crossed_boundary = False
            for catalog_id, state in catalog_state.items():
                known_catalogs.add(catalog_id)
                category = next(
                    (
                        name
                        for name, identifier in catalog_name_to_id.items()
                        if identifier == catalog_id
                    ),
                    "",
                )
                dates = [item.published_at for item in by_catalog.get(category, [])]
                if dates and min(dates) < run.range_start:
                    covered_catalogs.add(catalog_id)
                    crossed_boundary = True
                total = int(state["total"])
                if page_number * BINANCE_PAGE_SIZE >= total:
                    covered_catalogs.add(catalog_id)

            complete = bool(known_catalogs) and known_catalogs <= covered_catalogs
            stop_reason = ""
            if complete:
                if crossed_boundary:
                    stop_reason = NewsCollectionDiagnostic.StopReason.REACHED_TIME_BOUNDARY
                elif any(
                    item.published_at > run.range_start for item in parsed
                ):
                    stop_reason = NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
                else:
                    stop_reason = NewsCollectionDiagnostic.StopReason.NO_NEXT_PAGE
            published = [item.published_at for item in parsed]
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"binance_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                candidate_count=len(parsed) + invalid,
                parsed_count=len(parsed),
                eligible_count=len(eligible),
                inserted_count=counts.inserted,
                updated_count=counts.updated,
                duplicate_count=counts.duplicate,
                invalid_count=invalid,
                earliest_published_at=min(published) if published else None,
                latest_published_at=max(published) if published else None,
                stop_reason=stop_reason,
                coverage_complete=complete,
            )
            if complete:
                return
        except NewsCollectionError as exc:
            stop_reason = (
                NewsCollectionDiagnostic.StopReason.PAGINATION_LOOP
                if exc.code == "pagination_loop"
                else NewsCollectionDiagnostic.StopReason.REQUEST_FAILED
            )
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"binance_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                stop_reason=stop_reason,
                error_code=exc.code,
                error_summary=str(exc)[:500],
            )
            raise

    last = NewsCollectionDiagnostic.objects.filter(collection_run=run).first()
    if last:
        last.stop_reason = NewsCollectionDiagnostic.StopReason.SAFETY_PAGE_LIMIT
        last.coverage_complete = False
        last.error_code = "safety_page_limit"
        last.error_summary = "达到安全页数上限，仍未覆盖读取起点。"
        last.save(
            update_fields=[
                "stop_reason",
                "coverage_complete",
                "error_code",
                "error_summary",
            ]
        )
    raise NewsCollectionError(
        "达到 Binance 安全页数上限，覆盖不完整。", code="safety_page_limit"
    )


def _collect_tether(
    run: CollectionRun,
    source: NewsSource,
    feed: NewsFeed,
    client: NewsRequestClient,
    *,
    safety_page_limit: int,
) -> None:
    seen_page_hashes: set[str] = set()
    bootstrap = feed.bootstrap_visible_items and feed.trusted_coverage_end is None
    for page_number in range(1, safety_page_limit + 1):
        fetch = None
        try:
            params = {**TETHER_LIST_PARAMS, "page": page_number}
            fetch = client.get(feed.feed_url, params=params)
            parsed, invalid = parse_tether_page(fetch.response.content)
            if not parsed:
                code = "zero_first_page" if page_number == 1 else "zero_page"
                raise NewsCollectionError(
                    "Tether 新闻页面解析为 0 条，疑似解析器失效。",
                    code=code,
                )
            page_identity = _sha256(
                _stable_json(sorted(item.source_item_id for item in parsed))
            )
            if page_identity in seen_page_hashes:
                raise NewsCollectionError(
                    "Tether 分页返回重复页面。", code="pagination_loop"
                )
            seen_page_hashes.add(page_identity)

            total_pages_text = fetch.response.headers.get("X-WP-TotalPages", "")
            try:
                total_pages = max(int(total_pages_text), 1)
            except (TypeError, ValueError) as exc:
                raise NewsCollectionError(
                    "Tether 新闻接口缺少有效分页信息。", code="schema_changed"
                ) from exc

            eligible = [
                item
                for item in parsed
                if (bootstrap and item.published_at < run.range_end)
                or run.range_start <= item.published_at < run.range_end
            ]
            counts = SaveCounts()
            seen_at = timezone.now()
            for item in eligible:
                counts += save_news_item(
                    source=source,
                    feed=feed,
                    item=item,
                    run=run,
                    seen_at=seen_at,
                )

            published = [item.published_at for item in parsed]
            crossed_boundary = min(published) < run.range_start
            reached_last_page = page_number >= total_pages
            complete = bootstrap or crossed_boundary or reached_last_page
            stop_reason = ""
            if bootstrap:
                stop_reason = NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            elif crossed_boundary:
                stop_reason = NewsCollectionDiagnostic.StopReason.REACHED_TIME_BOUNDARY
            elif reached_last_page:
                stop_reason = NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"tether_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                candidate_count=len(parsed) + invalid,
                parsed_count=len(parsed),
                eligible_count=len(eligible),
                inserted_count=counts.inserted,
                updated_count=counts.updated,
                duplicate_count=counts.duplicate,
                invalid_count=invalid,
                earliest_published_at=min(published),
                latest_published_at=max(published),
                stop_reason=stop_reason,
                coverage_complete=complete,
                details={
                    "total_pages": total_pages,
                    "limited_initialization": bootstrap,
                },
            )
            if complete:
                return
        except NewsCollectionError as exc:
            stop_reason = (
                NewsCollectionDiagnostic.StopReason.PAGINATION_LOOP
                if exc.code == "pagination_loop"
                else NewsCollectionDiagnostic.StopReason.REQUEST_FAILED
            )
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"tether_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                stop_reason=stop_reason,
                error_code=exc.code,
                error_summary=str(exc)[:500],
            )
            raise

    last = NewsCollectionDiagnostic.objects.filter(collection_run=run).first()
    if last:
        last.stop_reason = NewsCollectionDiagnostic.StopReason.SAFETY_PAGE_LIMIT
        last.coverage_complete = False
        last.error_code = "safety_page_limit"
        last.error_summary = "达到 Tether 安全页数上限，仍未覆盖读取起点。"
        last.save(
            update_fields=[
                "stop_reason",
                "coverage_complete",
                "error_code",
                "error_summary",
            ]
        )
    raise NewsCollectionError(
        "达到 Tether 安全页数上限，覆盖不完整。", code="safety_page_limit"
    )


def _collect_slowmist_hacked(
    run: CollectionRun,
    source: NewsSource,
    feed: NewsFeed,
    client: NewsRequestClient,
    *,
    safety_page_limit: int,
) -> None:
    seen_page_hashes: set[str] = set()
    bootstrap = feed.bootstrap_visible_items and feed.trusted_coverage_end is None
    for page_number in range(1, safety_page_limit + 1):
        fetch = None
        try:
            params = {**SLOWMIST_LIST_PARAMS, "page": page_number}
            fetch = client.get(feed.feed_url, params=params)
            parsed, invalid, total_pages = parse_slowmist_hacked_page(
                fetch.response.content,
                page_number=page_number,
            )
            if not parsed:
                code = "zero_first_page" if page_number == 1 else "zero_page"
                raise NewsCollectionError(
                    "SlowMist Hacked 页面解析为 0 条，疑似解析器失效。",
                    code=code,
                )
            page_identity = _sha256(
                _stable_json(sorted(item.source_item_id for item in parsed))
            )
            if page_identity in seen_page_hashes:
                raise NewsCollectionError(
                    "SlowMist Hacked 分页返回重复页面。", code="pagination_loop"
                )
            seen_page_hashes.add(page_identity)

            eligible = [
                item
                for item in parsed
                if (bootstrap and item.published_at < run.range_end)
                or run.range_start <= item.published_at < run.range_end
            ]
            counts = SaveCounts()
            seen_at = timezone.now()
            for item in eligible:
                counts += save_news_item(
                    source=source,
                    feed=feed,
                    item=item,
                    run=run,
                    seen_at=seen_at,
                )

            published = [item.published_at for item in parsed]
            crossed_boundary = min(published) < run.range_start
            reached_last_page = page_number >= total_pages
            complete = bootstrap or crossed_boundary or reached_last_page
            stop_reason = ""
            if bootstrap:
                stop_reason = NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            elif crossed_boundary:
                stop_reason = NewsCollectionDiagnostic.StopReason.REACHED_TIME_BOUNDARY
            elif reached_last_page:
                stop_reason = NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"slowmist_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                candidate_count=len(parsed) + invalid,
                parsed_count=len(parsed),
                eligible_count=len(eligible),
                inserted_count=counts.inserted,
                updated_count=counts.updated,
                duplicate_count=counts.duplicate,
                invalid_count=invalid,
                earliest_published_at=min(published),
                latest_published_at=max(published),
                stop_reason=stop_reason,
                coverage_complete=complete,
                details={
                    "total_pages": total_pages,
                    "limited_initialization": bootstrap,
                },
            )
            if complete:
                return
        except NewsCollectionError as exc:
            stop_reason = (
                NewsCollectionDiagnostic.StopReason.PAGINATION_LOOP
                if exc.code == "pagination_loop"
                else NewsCollectionDiagnostic.StopReason.REQUEST_FAILED
            )
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"slowmist_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                stop_reason=stop_reason,
                error_code=exc.code,
                error_summary=str(exc)[:500],
            )
            raise

    last = NewsCollectionDiagnostic.objects.filter(collection_run=run).first()
    if last:
        last.stop_reason = NewsCollectionDiagnostic.StopReason.SAFETY_PAGE_LIMIT
        last.coverage_complete = False
        last.error_code = "safety_page_limit"
        last.error_summary = "达到 SlowMist Hacked 安全页数上限，仍未覆盖读取起点。"
        last.save(
            update_fields=[
                "stop_reason",
                "coverage_complete",
                "error_code",
                "error_summary",
            ]
        )
    raise NewsCollectionError(
        "达到 SlowMist Hacked 安全页数上限，覆盖不完整。",
        code="safety_page_limit",
    )


def _collect_circle_pressroom(
    run: CollectionRun,
    source: NewsSource,
    feed: NewsFeed,
    client: NewsRequestClient,
    *,
    safety_page_limit: int,
) -> None:
    seen_page_hashes: set[str] = set()
    bootstrap = feed.bootstrap_visible_items and feed.trusted_coverage_end is None
    page_url = feed.feed_url
    for page_number in range(1, safety_page_limit + 1):
        fetch = None
        try:
            fetch = client.get(page_url)
            parsed, invalid, next_page_url, total_pages = parse_circle_pressroom_page(
                fetch.response.content
            )
            if not parsed:
                code = "zero_first_page" if page_number == 1 else "zero_page"
                raise NewsCollectionError(
                    "Circle Pressroom 页面解析为 0 条，疑似页面结构已变化。",
                    code=code,
                )
            if page_number < total_pages and not next_page_url:
                raise NewsCollectionError(
                    "Circle Pressroom 缺少预期的下一页链接。",
                    code="schema_changed",
                )
            page_identity = _sha256(
                _stable_json(sorted(item.source_item_id for item in parsed))
            )
            if page_identity in seen_page_hashes:
                raise NewsCollectionError(
                    "Circle Pressroom 分页返回重复页面。",
                    code="pagination_loop",
                )
            seen_page_hashes.add(page_identity)

            eligible = [
                item
                for item in parsed
                if (bootstrap and item.published_at < run.range_end)
                or run.range_start <= item.published_at < run.range_end
            ]
            counts = SaveCounts()
            seen_at = timezone.now()
            detail_failure_count = 0
            detail_request_count = 0
            detail_retry_count = 0
            detail_failure_items: list[str] = []
            for item in eligible:
                article_fetch = None
                try:
                    article_fetch = client.get(item.original_url)
                    detail_request_count += article_fetch.request_count
                    detail_retry_count += article_fetch.retry_count
                    article_text = parse_circle_pressroom_article(
                        article_fetch.response.content
                    )
                    item = replace(
                        item,
                        raw_payload={
                            **item.raw_payload,
                            "article_text": article_text,
                            "article_fetch_status": "fetched",
                        },
                    )
                except NewsCollectionError as exc:
                    if article_fetch is None:
                        detail_request_count += client.last_request_count
                        detail_retry_count += client.last_retry_count
                    detail_failure_count += 1
                    detail_failure_items.append(item.source_item_id)
                    item = replace(
                        item,
                        raw_payload={
                            **item.raw_payload,
                            "article_text": "",
                            "article_fetch_status": "failed",
                            "article_error_code": exc.code,
                        },
                    )
                counts += save_news_item(
                    source=source,
                    feed=feed,
                    item=item,
                    run=run,
                    seen_at=seen_at,
                )

            published = [item.published_at for item in parsed]
            crossed_boundary = min(published) < run.range_start
            reached_last_page = not next_page_url or page_number >= total_pages
            complete = bootstrap or crossed_boundary or reached_last_page
            if bootstrap:
                stop_reason = NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            elif crossed_boundary:
                stop_reason = NewsCollectionDiagnostic.StopReason.REACHED_TIME_BOUNDARY
            elif reached_last_page:
                stop_reason = NewsCollectionDiagnostic.StopReason.NO_NEXT_PAGE
            else:
                stop_reason = ""
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"circle_pressroom_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                request_count_override=fetch.request_count + detail_request_count,
                retry_count_override=fetch.retry_count + detail_retry_count,
                candidate_count=len(parsed) + invalid,
                parsed_count=len(parsed),
                eligible_count=len(eligible),
                inserted_count=counts.inserted,
                updated_count=counts.updated,
                duplicate_count=counts.duplicate,
                invalid_count=invalid,
                earliest_published_at=min(published),
                latest_published_at=max(published),
                stop_reason=stop_reason,
                coverage_complete=complete,
                details={
                    "total_pages": total_pages,
                    "limited_initialization": bootstrap,
                    "detail_fetch_failure_count": detail_failure_count,
                    "detail_failure_items": detail_failure_items,
                },
                error_summary=(
                    f"Circle 有 {detail_failure_count} 条新闻正文读取失败，已保留列表摘要。"
                    if detail_failure_count
                    else ""
                ),
            )
            if complete:
                return
            page_url = next_page_url
        except NewsCollectionError as exc:
            stop_reason = (
                NewsCollectionDiagnostic.StopReason.PAGINATION_LOOP
                if exc.code == "pagination_loop"
                else NewsCollectionDiagnostic.StopReason.REQUEST_FAILED
            )
            _create_diagnostic(
                run=run,
                source=source,
                feed=feed,
                unit_type=NewsCollectionDiagnostic.UnitType.PAGE,
                unit_identifier=f"circle_pressroom_page_{page_number}",
                client=client,
                fetch=fetch,
                page_number=page_number,
                stop_reason=stop_reason,
                error_code=exc.code,
                error_summary=str(exc)[:500],
            )
            raise

    last = NewsCollectionDiagnostic.objects.filter(collection_run=run).first()
    if last:
        last.stop_reason = NewsCollectionDiagnostic.StopReason.SAFETY_PAGE_LIMIT
        last.coverage_complete = False
        last.error_code = "safety_page_limit"
        last.error_summary = "Circle Pressroom 达到 5 页安全上限，仍未覆盖读取起点。"
        last.save(
            update_fields=[
                "stop_reason",
                "coverage_complete",
                "error_code",
                "error_summary",
            ]
        )
    raise NewsCollectionError(
        "Circle Pressroom 达到安全页数上限，覆盖不完整。",
        code="safety_page_limit",
    )


def _default_request_client(feed: NewsFeed) -> NewsRequestClient:
    if feed.code in SEC_FEED_CODES:
        return NewsRequestClient(
            user_agent=settings.SEC_NEWS_USER_AGENT,
            rate_limit_key="sec.gov",
            min_request_interval_seconds=settings.SEC_NEWS_MIN_REQUEST_INTERVAL_SECONDS,
        )
    if feed.code == TETHER_NEWS_CODE:
        return NewsRequestClient(
            user_agent=settings.NEWS_COLLECTOR_USER_AGENT,
            rate_limit_key="tether.io",
            min_request_interval_seconds=settings.TETHER_NEWS_MIN_REQUEST_INTERVAL_SECONDS,
        )
    if feed.code == SLOWMIST_HACKED_CODE:
        return NewsRequestClient(
            user_agent=settings.NEWS_COLLECTOR_USER_AGENT,
            rate_limit_key="hacked.slowmist.io",
            min_request_interval_seconds=settings.SLOWMIST_HACKED_MIN_REQUEST_INTERVAL_SECONDS,
        )
    if feed.code == CIRCLE_PRESSROOM_CODE:
        return NewsRequestClient(
            user_agent=settings.NEWS_COLLECTOR_USER_AGENT,
            rate_limit_key="circle.com",
            min_request_interval_seconds=settings.CIRCLE_PRESSROOM_MIN_REQUEST_INTERVAL_SECONDS,
        )
    return NewsRequestClient(user_agent=settings.NEWS_COLLECTOR_USER_AGENT)


def collect_news_feed(
    feed_code: str,
    *,
    trigger: str = CollectionRun.Trigger.MANUAL,
    range_end: datetime | None = None,
    client: NewsRequestClient | None = None,
    safety_page_limit: int = max(
        BINANCE_SAFETY_PAGE_LIMIT,
        TETHER_SAFETY_PAGE_LIMIT,
        SLOWMIST_SAFETY_PAGE_LIMIT,
    ),
) -> CollectionRun:
    feed = NewsFeed.objects.select_related("source").get(code=feed_code)
    source = feed.source
    if not source.enabled:
        raise ValueError(f"News source is disabled: {source.code}")
    if not feed.enabled:
        raise ValueError(f"News feed is disabled: {feed_code}")
    definition = FEED_DEFINITIONS.get(feed_code)
    if definition is None:
        raise ValueError(f"Unsupported news feed: {feed_code}")
    range_end = range_end or timezone.now()
    range_start, range_end = collection_window(feed, range_end)
    if range_start >= range_end:
        raise ValueError("News collection range must be positive.")
    run = CollectionRun.objects.create(
        data_type=CollectionRun.DataType.NEWS,
        news_source=source,
        news_feed=feed,
        exchange="",
        market_type="",
        symbol="",
        interval="",
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        status=CollectionRun.Status.RUNNING,
        started_at=range_end,
    )
    request_client = client or _default_request_client(feed)
    owns_client = client is None
    try:
        if definition.collection_method == "rss":
            _collect_rss(run, source, feed, request_client)
        elif feed_code == BINANCE_ANNOUNCEMENTS_CODE:
            _collect_binance(
                run,
                source,
                feed,
                request_client,
                safety_page_limit=safety_page_limit,
            )
        elif feed_code == TETHER_NEWS_CODE:
            _collect_tether(
                run,
                source,
                feed,
                request_client,
                safety_page_limit=safety_page_limit,
            )
        elif feed_code == SLOWMIST_HACKED_CODE:
            _collect_slowmist_hacked(
                run,
                source,
                feed,
                request_client,
                safety_page_limit=safety_page_limit,
            )
        elif feed_code == CIRCLE_PRESSROOM_CODE:
            _collect_circle_pressroom(
                run,
                source,
                feed,
                request_client,
                safety_page_limit=min(safety_page_limit, CIRCLE_SAFETY_PAGE_LIMIT),
            )
        run.status = CollectionRun.Status.SUCCESS
    except Exception as exc:
        persisted = NewsRawRecord.objects.filter(last_collection_run=run).count()
        run.status = (
            CollectionRun.Status.PARTIAL if persisted else CollectionRun.Status.FAILED
        )
        run.error_message = (
            str(exc)[:500]
            if isinstance(exc, (NewsCollectionError, ValueError))
            else f"{exc.__class__.__name__}: collection failed"
        )
    finally:
        diagnostics = NewsCollectionDiagnostic.objects.filter(collection_run=run)
        run.finished_at = timezone.now()
        run.request_count = sum(d.request_count for d in diagnostics)
        run.received_count = sum(d.candidate_count for d in diagnostics)
        run.inserted_count = sum(d.inserted_count for d in diagnostics)
        run.updated_count = sum(d.updated_count for d in diagnostics)
        run.skipped_count = sum(d.duplicate_count for d in diagnostics)
        run.failed_count = sum(d.invalid_count for d in diagnostics) + (
            1 if run.status != CollectionRun.Status.SUCCESS else 0
        )
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "request_count",
                "received_count",
                "inserted_count",
                "updated_count",
                "skipped_count",
                "failed_count",
                "error_message",
            ]
        )
        if owns_client:
            request_client.close()
    return run


def collect_news_source(
    source_code: str,
    *,
    trigger: str = CollectionRun.Trigger.MANUAL,
    range_end: datetime | None = None,
    client: NewsRequestClient | None = None,
    safety_page_limit: int = max(
        BINANCE_SAFETY_PAGE_LIMIT,
        TETHER_SAFETY_PAGE_LIMIT,
        SLOWMIST_SAFETY_PAGE_LIMIT,
    ),
) -> CollectionRun:
    """Compatibility entry for sources that contain exactly one enabled feed."""
    source = NewsSource.objects.get(code=source_code)
    feeds = list(source.feeds.filter(enabled=True))
    if len(feeds) != 1:
        raise ValueError(
            f"News source requires a feed-specific collection entry: {source_code}"
        )
    feed = feeds[0]
    if feed.code == source.code:
        legacy_fields = (
            "activated_at",
            "last_run_at",
            "trusted_coverage_end",
            "last_inspection_status",
            "health_status",
        )
        changed = False
        for field in legacy_fields:
            value = getattr(source, field)
            if getattr(feed, field) != value:
                setattr(feed, field, value)
                changed = True
        if changed:
            feed.save(update_fields=[*legacy_fields, "updated_at"])
    return collect_news_feed(
        feed.code,
        trigger=trigger,
        range_end=range_end,
        client=client,
        safety_page_limit=safety_page_limit,
    )
