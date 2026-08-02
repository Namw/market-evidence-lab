from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from django.test import TestCase

from apps.collection.models import CollectionRun
from apps.collection.pipeline import collect_and_inspect
from apps.inspection.models import NewsInspectionRun
from apps.news_data.collectors import (
    NewsRequestClient,
    ParsedNewsItem,
    parse_rss_feed,
    parse_slowmist_hacked_page,
    parse_tether_page,
)
from apps.news_data.models import (
    NewsCollectionDiagnostic,
    NewsFeed,
    NewsRawRecord,
    NewsSource,
)
from apps.news_data.services import (
    _default_request_client,
    collection_window,
    save_news_item,
)
from apps.news_data.sources import (
    BINANCE_ANNOUNCEMENTS_CODE,
    ETHEREUM_FOUNDATION_CODE,
    SEC_LITIGATION_RELEASES_CODE,
    SLOWMIST_HACKED_CODE,
    TETHER_NEWS_CODE,
)


FIXTURES = Path(__file__).parent / "fixtures"
END = datetime(2026, 8, 1, tzinfo=UTC)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def request_client(handler) -> NewsRequestClient:
    return NewsRequestClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


def response_for(content: bytes, content_type: str):
    def handler(request):
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": content_type},
            request=request,
        )

    return handler


def tether_post(
    item_id: int,
    published_at: str,
    *,
    title: str = "Tether update",
) -> dict[str, object]:
    return {
        "id": item_id,
        "date_gmt": published_at,
        "modified_gmt": published_at,
        "slug": f"tether-update-{item_id}",
        "status": "publish",
        "type": "post",
        "link": f"https://tether.io/news/tether-update-{item_id}/",
        "title": {"rendered": title},
        "excerpt": {"rendered": "<p>Official &amp; structured summary [&hellip;]</p>"},
        "content": {"rendered": "Full article body must not be saved."},
        "author": 1,
        "categories": [3, 6],
        "tags": [9],
        "reading_time": "3 minutes read",
        "_embedded": {
            "author": [{"id": 1, "name": "Tether"}],
            "wp:term": [
                [
                    {"id": 3, "name": "News", "taxonomy": "category"},
                    {"id": 6, "name": "Finance", "taxonomy": "category"},
                ],
                [{"id": 9, "name": "USDt", "taxonomy": "post_tag"}],
            ],
        },
    }


def slowmist_page(page_number: int, event_date: str, target: str) -> bytes:
    return f"""<!doctype html><html><body>
    <div class='case-content'><ul><li>
    <span class='time'>{event_date}</span>
    <h3><em>Hacked target: </em>{target}</h3>
    <p><em>Description of the event: </em>{target} security event.</p>
    <p><span><em>Amount of loss: </em>$ 10</span>
    <span><em>Attack method: </em>Test Attack</span></p>
    <p class='link-reference'><a href='https://example.com/{target}'>Reference</a></p>
    </li></ul></div>
    <ul class='pagination'><li>Page {page_number} of 10</li>
    <li><a href='/?c=&amp;page=10'>Last</a></li></ul>
    </body></html>""".encode()


class NewsCollectionTests(TestCase):
    def setUp(self):
        self.ef = NewsSource.objects.get(code=ETHEREUM_FOUNDATION_CODE)
        self.ef.activated_at = datetime(2026, 7, 31, tzinfo=UTC)
        self.ef.trusted_coverage_end = None
        self.ef.last_run_at = None
        self.ef.last_inspection_status = NewsSource.InspectionStatus.NEVER_RUN
        self.ef.health_status = NewsSource.HealthStatus.NEVER_RUN
        self.ef.save()
        self.binance = NewsSource.objects.get(code=BINANCE_ANNOUNCEMENTS_CODE)
        self.binance.activated_at = datetime(2026, 7, 28, tzinfo=UTC)
        self.binance.trusted_coverage_end = None
        self.binance.last_run_at = None
        self.binance.last_inspection_status = NewsSource.InspectionStatus.NEVER_RUN
        self.binance.health_status = NewsSource.HealthStatus.NEVER_RUN
        self.binance.save()
        self.tether = NewsSource.objects.get(code=TETHER_NEWS_CODE)
        self.tether_feed = NewsFeed.objects.get(code=TETHER_NEWS_CODE)
        self.tether_feed.activated_at = END - timedelta(minutes=1)
        self.tether_feed.trusted_coverage_end = None
        self.tether_feed.save(update_fields=["activated_at", "trusted_coverage_end"])
        self.slowmist = NewsSource.objects.get(code=SLOWMIST_HACKED_CODE)
        self.slowmist_feed = NewsFeed.objects.get(code=SLOWMIST_HACKED_CODE)
        self.slowmist_feed.activated_at = END - timedelta(minutes=1)
        self.slowmist_feed.trusted_coverage_end = None
        self.slowmist_feed.save(update_fields=["activated_at", "trusted_coverage_end"])

    def test_first_run_only_accepts_items_at_or_after_activation(self):
        result = collect_and_inspect(
            data_type=CollectionRun.DataType.NEWS,
            source_code=self.ef.code,
            range_end=END,
            client=request_client(
                response_for(fixture("ethereum_feed.xml"), "application/xml")
            ),
        )

        self.assertEqual(result.collection_run.range_start, self.ef.activated_at)
        self.assertEqual(NewsRawRecord.objects.count(), 2)
        self.assertFalse(NewsRawRecord.objects.filter(source_item_id="ef-before").exists())
        self.assertEqual(result.inspection_run.quality_status, "passed")

    def test_subsequent_window_uses_trusted_watermark_minus_three_days(self):
        self.ef.activated_at = datetime(2026, 7, 1, tzinfo=UTC)
        self.ef.trusted_coverage_end = END
        self.ef.save(update_fields=["activated_at", "trusted_coverage_end"])

        start, end = collection_window(self.ef, END + timedelta(days=1))

        self.assertEqual(start, END - timedelta(days=3))
        self.assertEqual(end, END + timedelta(days=1))

    def test_failed_run_does_not_advance_watermark(self):
        old_watermark = datetime(2026, 7, 31, tzinfo=UTC)
        self.ef.activated_at = datetime(2026, 7, 1, tzinfo=UTC)
        self.ef.trusted_coverage_end = old_watermark
        self.ef.save(update_fields=["activated_at", "trusted_coverage_end"])

        result = collect_and_inspect(
            data_type="news",
            source_code=self.ef.code,
            range_end=END,
            client=request_client(
                response_for(fixture("ethereum_empty.xml"), "application/xml")
            ),
        )

        self.assertEqual(result.inspection_run.quality_status, "failed")
        self.ef.refresh_from_db()
        self.assertEqual(self.ef.trusted_coverage_end, old_watermark)
        self.assertEqual(self.ef.health_status, "broken")

    def test_warning_with_complete_visible_history_advances_watermark(self):
        self.ef.activated_at = datetime(2026, 7, 1, tzinfo=UTC)
        self.ef.save(update_fields=["activated_at"])

        result = collect_and_inspect(
            data_type="news",
            source_code=self.ef.code,
            range_end=END,
            client=request_client(
                response_for(fixture("ethereum_feed.xml"), "application/xml")
            ),
        )

        self.assertEqual(result.inspection_run.quality_status, "warning")
        self.assertTrue(result.inspection_run.coverage_complete)
        self.ef.refresh_from_db()
        self.assertEqual(self.ef.trusted_coverage_end, END)
        self.assertEqual(self.ef.health_status, "degraded")

    def test_success_after_retry_is_warning_but_still_complete(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(500, request=request)
            return httpx.Response(
                200,
                content=fixture("ethereum_feed.xml"),
                headers={"content-type": "application/xml"},
                request=request,
            )

        client = NewsRequestClient(
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            max_retries=1,
            sleep_fn=lambda _seconds: None,
        )
        result = collect_and_inspect(
            data_type="news",
            source_code=self.ef.code,
            range_end=END,
            client=client,
        )

        self.assertEqual(result.inspection_run.quality_status, "warning")
        self.assertTrue(result.inspection_run.coverage_complete)
        self.assertEqual(result.collection_run.news_diagnostics.get().retry_count, 1)

    def test_health_uses_36_and_72_hour_freshness_thresholds(self):
        self.ef.last_run_at = END
        self.ef.trusted_coverage_end = END
        self.ef.last_inspection_status = NewsSource.InspectionStatus.PASSED

        self.assertEqual(
            self.ef.health_at(END + timedelta(hours=36)),
            NewsSource.HealthStatus.HEALTHY,
        )
        self.assertEqual(
            self.ef.health_at(END + timedelta(hours=37)),
            NewsSource.HealthStatus.DEGRADED,
        )
        self.assertEqual(
            self.ef.health_at(END + timedelta(hours=73)),
            NewsSource.HealthStatus.BROKEN,
        )

    def test_rss_repeat_is_normal_zero_new_and_idempotent(self):
        client = request_client(
            response_for(fixture("ethereum_feed.xml"), "application/xml")
        )
        collect_and_inspect(
            data_type="news", source_code=self.ef.code, range_end=END, client=client
        )
        result = collect_and_inspect(
            data_type="news", source_code=self.ef.code, range_end=END, client=client
        )

        self.assertEqual(NewsRawRecord.objects.count(), 2)
        self.assertEqual(result.collection_run.inserted_count, 0)
        self.assertEqual(result.inspection_run.duplicate_count, 2)
        self.assertEqual(result.inspection_run.quality_status, "passed")

    def test_rss_zero_parseable_items_fails(self):
        result = collect_and_inspect(
            data_type="news",
            source_code=self.ef.code,
            range_end=END,
            client=request_client(
                response_for(fixture("ethereum_empty.xml"), "application/xml")
            ),
        )

        self.assertEqual(result.collection_run.status, "failed")
        self.assertEqual(result.inspection_run.quality_status, "failed")
        self.assertIn("可解析", "".join(result.inspection_run.reasons))

    def test_regulator_rss_recovers_bare_ampersand_and_bootstraps_visible_items(self):
        feed = NewsFeed.objects.get(code=SEC_LITIGATION_RELEASES_CODE)
        feed.activated_at = END - timedelta(minutes=1)
        feed.trusted_coverage_end = None
        feed.save(update_fields=["activated_at", "trusted_coverage_end"])
        content = b"""<?xml version='1.0' encoding='utf-8'?>
        <rss xmlns:dc='http://purl.org/dc/elements/1.1/' version='2.0'><channel>
        <item><title>DJ&S Property enforcement</title>
        <link>https://www.sec.gov/enforcement-litigation/litigation-releases/lr-1</link>
        <description>Official summary</description>
        <pubDate>Thu, 30 Jul 2026 09:53:38 -0400</pubDate>
        <dc:creator>LR-1</dc:creator><guid>sec-lr-1</guid></item>
        </channel></rss>"""

        result = collect_and_inspect(
            data_type="news",
            feed_code=feed.code,
            range_end=END,
            client=request_client(response_for(content, "application/rss+xml")),
        )

        record = NewsRawRecord.objects.get(source_item_id="sec-lr-1")
        self.assertEqual(record.source_author, "LR-1")
        self.assertEqual(record.source_category, feed.name)
        self.assertTrue(record.feeds.filter(pk=feed.pk).exists())
        self.assertEqual(result.collection_run.inserted_count, 1)
        self.assertEqual(result.inspection_run.quality_status, "warning")
        self.assertTrue(
            result.collection_run.news_diagnostics.get().details["xml_recovered"]
        )

    def test_generic_rss_parser_isolates_invalid_items(self):
        content = b"""<rss version='2.0'><channel>
        <item><title>Valid item</title><link>https://example.com/valid</link>
        <pubDate>Thu, 30 Jul 2026 09:53:38 -0400</pubDate><guid>valid</guid></item>
        <item><title>Missing publication date</title><link>https://example.com/bad</link></item>
        </channel></rss>"""

        parsed, invalid, recovered = parse_rss_feed(content, feed_category="新闻稿")

        self.assertEqual([item.source_item_id for item in parsed], ["valid"])
        self.assertEqual(invalid, 1)
        self.assertFalse(recovered)

    def test_tether_parser_uses_excerpt_and_omits_article_body(self):
        payload = [
            tether_post(
                2846,
                "2026-07-31T15:00:00",
                title="Tether &amp; official update",
            ),
            {"id": 2, "title": {"rendered": "Missing fields"}},
        ]

        parsed, invalid = parse_tether_page(json.dumps(payload).encode())

        self.assertEqual(invalid, 1)
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertEqual(item.source_item_id, "2846")
        self.assertEqual(item.title, "Tether & official update")
        self.assertEqual(item.summary, "Official & structured summary […]")
        self.assertEqual(item.source_author, "Tether")
        self.assertEqual(item.source_category, "Finance")
        self.assertEqual(item.source_tags, ["News", "Finance", "USDt"])
        self.assertNotIn("content", item.raw_payload)

    def test_tether_first_run_only_bootstraps_newest_api_page(self):
        payload = [
            tether_post(1, "2026-07-30T12:00:00"),
            tether_post(2, "2026-07-29T12:00:00"),
        ]

        def handler(request):
            self.assertEqual(request.url.params["categories"], "3")
            self.assertEqual(request.url.params["page"], "1")
            return httpx.Response(
                200,
                json=payload,
                headers={"X-WP-TotalPages": "22"},
                request=request,
            )

        result = collect_and_inspect(
            data_type="news",
            feed_code=TETHER_NEWS_CODE,
            range_end=END,
            client=request_client(handler),
        )

        diagnostic = result.collection_run.news_diagnostics.get()
        self.assertEqual(result.collection_run.request_count, 1)
        self.assertEqual(result.collection_run.inserted_count, 2)
        self.assertEqual(diagnostic.stop_reason, "source_history_limited")
        self.assertTrue(diagnostic.coverage_complete)
        self.assertTrue(diagnostic.details["limited_initialization"])
        self.assertEqual(result.inspection_run.quality_status, "warning")

    def test_tether_request_client_has_source_rate_limit(self):
        client = _default_request_client(self.tether_feed)
        try:
            self.assertEqual(client.rate_limit_key, "tether.io")
            self.assertGreaterEqual(client.min_request_interval_seconds, 1.0)
        finally:
            client.close()

    def test_slowmist_parser_extracts_event_fields_and_isolates_invalid_items(self):
        parsed, invalid, total_pages = parse_slowmist_hacked_page(
            fixture("slowmist_hacked_page.html"),
            page_number=1,
        )

        self.assertEqual(len(parsed), 2)
        self.assertEqual(invalid, 1)
        self.assertEqual(total_pages, 111)
        item = parsed[0]
        self.assertTrue(item.source_item_id.startswith("slowmist-"))
        self.assertEqual(item.title, "Hacked target: Ethereum Bridge")
        self.assertEqual(item.summary, "An Ethereum bridge was exploited & paused.")
        self.assertEqual(item.source_category, "Smart Contract Vulnerability")
        self.assertEqual(
            item.source_tags,
            ["Security incident", "Smart Contract Vulnerability"],
        )
        self.assertEqual(item.source_author, "SlowMist")
        self.assertEqual(item.occurred_at, datetime(2026, 7, 30, tzinfo=UTC))
        self.assertFalse(item.canonical_url_supported)
        self.assertEqual(item.raw_payload["amount_of_loss"], "$ 1,250,000")

    def test_slowmist_first_run_only_bootstraps_newest_page(self):
        def handler(request):
            self.assertEqual(request.url.params["c"], "")
            self.assertEqual(request.url.params["page"], "1")
            return httpx.Response(
                200,
                content=fixture("slowmist_hacked_page.html"),
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )

        result = collect_and_inspect(
            data_type="news",
            feed_code=SLOWMIST_HACKED_CODE,
            range_end=END,
            client=request_client(handler),
        )

        diagnostic = result.collection_run.news_diagnostics.get()
        records = NewsRawRecord.objects.filter(source=self.slowmist)
        self.assertEqual(result.collection_run.request_count, 1)
        self.assertEqual(result.collection_run.inserted_count, 2)
        self.assertEqual(records.count(), 2)
        self.assertTrue(all(record.canonical_url == "" for record in records))
        self.assertEqual(diagnostic.invalid_count, 1)
        self.assertEqual(diagnostic.stop_reason, "source_history_limited")
        self.assertTrue(diagnostic.coverage_complete)
        self.assertTrue(diagnostic.details["limited_initialization"])
        self.assertEqual(result.inspection_run.quality_status, "warning")

    def test_slowmist_request_client_has_source_rate_limit(self):
        client = _default_request_client(self.slowmist_feed)
        try:
            self.assertEqual(client.rate_limit_key, "hacked.slowmist.io")
            self.assertGreaterEqual(client.min_request_interval_seconds, 1.0)
        finally:
            client.close()

    def test_slowmist_incremental_run_paginates_until_time_boundary(self):
        self.slowmist_feed.bootstrap_visible_items = False
        self.slowmist_feed.activated_at = datetime(2026, 7, 1, tzinfo=UTC)
        self.slowmist_feed.trusted_coverage_end = datetime(2026, 7, 31, tzinfo=UTC)
        self.slowmist_feed.save(
            update_fields=[
                "bootstrap_visible_items",
                "activated_at",
                "trusted_coverage_end",
            ]
        )
        pages = {
            "1": slowmist_page(1, "2026-07-30", "newer-event"),
            "2": slowmist_page(2, "2026-07-27", "older-event"),
        }

        def handler(request):
            return httpx.Response(
                200,
                content=pages[request.url.params["page"]],
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )

        result = collect_and_inspect(
            data_type="news",
            feed_code=SLOWMIST_HACKED_CODE,
            range_end=END,
            client=request_client(handler),
        )

        diagnostics = list(
            result.collection_run.news_diagnostics.order_by("page_number")
        )
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(diagnostics[-1].stop_reason, "reached_time_boundary")
        self.assertTrue(diagnostics[-1].coverage_complete)
        self.assertEqual(result.collection_run.inserted_count, 1)
        self.assertEqual(result.inspection_run.quality_status, "passed")

    def test_binance_paginates_until_older_than_boundary(self):
        pages = {
            "1": fixture("binance_page_1.json"),
            "2": fixture("binance_page_2.json"),
        }

        def handler(request):
            return httpx.Response(
                200,
                content=pages[request.url.params["pageNo"]],
                headers={"content-type": "application/json"},
                request=request,
            )

        result = collect_and_inspect(
            data_type="news",
            source_code=self.binance.code,
            range_end=END,
            client=request_client(handler),
        )

        diagnostics = list(
            result.collection_run.news_diagnostics.order_by("page_number")
        )
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(diagnostics[-1].stop_reason, "reached_time_boundary")
        self.assertTrue(diagnostics[-1].coverage_complete)
        self.assertEqual(result.inspection_run.quality_status, "passed")
        self.assertEqual(NewsRawRecord.objects.count(), 2)

    def test_binance_first_page_zero_items_fails(self):
        payload = json.dumps(
            {
                "code": "000000",
                "data": {"catalogs": [{"catalogId": 1, "total": 1, "articles": []}]},
            }
        ).encode()
        result = collect_and_inspect(
            data_type="news",
            source_code=self.binance.code,
            range_end=END,
            client=request_client(response_for(payload, "application/json")),
        )

        self.assertEqual(result.inspection_run.quality_status, "failed")
        self.assertEqual(
            result.collection_run.news_diagnostics.get().error_code,
            "zero_first_page",
        )

    def test_binance_repeated_page_is_pagination_loop(self):
        result = collect_and_inspect(
            data_type="news",
            source_code=self.binance.code,
            range_end=END,
            client=request_client(
                response_for(fixture("binance_page_1.json"), "application/json")
            ),
        )

        self.assertEqual(result.inspection_run.quality_status, "failed")
        self.assertTrue(
            result.collection_run.news_diagnostics.filter(
                stop_reason="pagination_loop"
            ).exists()
        )

    def test_binance_safety_limit_is_incomplete_and_failed(self):
        def handler(request):
            page = int(request.url.params["pageNo"])
            payload = {
                "code": "000000",
                "data": {
                    "catalogs": [
                        {
                            "catalogId": 1,
                            "catalogName": "Latest",
                            "total": 100,
                            "articles": [
                                {
                                    "id": page,
                                    "code": f"page-{page}",
                                    "title": f"Page {page}",
                                    "releaseDate": 1785495600000 - page,
                                }
                            ],
                        }
                    ]
                },
            }
            return httpx.Response(200, json=payload, request=request)

        result = collect_and_inspect(
            data_type="news",
            source_code=self.binance.code,
            range_end=END,
            client=request_client(handler),
            safety_page_limit=2,
        )

        final = result.collection_run.news_diagnostics.order_by("-page_number").first()
        self.assertEqual(final.stop_reason, "safety_page_limit")
        self.assertFalse(final.coverage_complete)
        self.assertEqual(result.inspection_run.quality_status, "failed")


class NewsDeduplicationTests(TestCase):
    def setUp(self):
        self.source = NewsSource.objects.get(code=ETHEREUM_FOUNDATION_CODE)
        self.run = CollectionRun.objects.create(
            data_type="news",
            news_source=self.source,
            range_start=datetime(2026, 7, 1, tzinfo=UTC),
            range_end=END,
            started_at=END,
            status="success",
        )

    def item(self, *, item_id="id-1", url="https://example.com/a", title="Title"):
        return ParsedNewsItem(
            source_item_id=item_id,
            original_url=url,
            title=title,
            summary="Summary",
            published_at=datetime(2026, 7, 30, tzinfo=UTC),
            updated_at_source=None,
            language="en",
            source_category="Category",
            source_tags=["Category"],
            raw_payload={"title": title},
        )

    def test_source_item_id_has_first_dedup_priority(self):
        save_news_item(source=self.source, item=self.item(), run=self.run, seen_at=END)
        count = save_news_item(
            source=self.source,
            item=self.item(url="https://example.com/changed"),
            run=self.run,
            seen_at=END + timedelta(minutes=1),
        )
        self.assertEqual(count.updated, 1)
        self.assertEqual(NewsRawRecord.objects.count(), 1)

    def test_canonical_url_is_second_dedup_priority(self):
        first = self.item(item_id="", url="https://example.com/a?utm_source=x#part")
        second = self.item(item_id="", url="https://example.com/a")
        save_news_item(source=self.source, item=first, run=self.run, seen_at=END)
        count = save_news_item(source=self.source, item=second, run=self.run, seen_at=END)
        self.assertEqual(count.updated + count.duplicate, 1)
        self.assertEqual(NewsRawRecord.objects.count(), 1)

    def test_time_and_stable_content_fingerprint_is_third_priority(self):
        item = self.item(item_id="", url="")
        save_news_item(source=self.source, item=item, run=self.run, seen_at=END)
        count = save_news_item(source=self.source, item=item, run=self.run, seen_at=END)
        self.assertEqual(count.duplicate, 1)
        self.assertEqual(NewsRawRecord.objects.count(), 1)

    def test_content_change_updates_content_hash(self):
        save_news_item(source=self.source, item=self.item(), run=self.run, seen_at=END)
        before = NewsRawRecord.objects.get().content_hash
        count = save_news_item(
            source=self.source,
            item=self.item(title="Updated title"),
            run=self.run,
            seen_at=END + timedelta(minutes=1),
        )
        record = NewsRawRecord.objects.get()
        self.assertEqual(count.updated, 1)
        self.assertNotEqual(record.content_hash, before)
        self.assertIsNone(record.occurred_at)

    def test_collection_and_inspection_are_linked_and_failures_have_reasons(self):
        diagnostic = NewsCollectionDiagnostic.objects.create(
            collection_run=self.run,
            source=self.source,
            unit_type="feed",
            unit_identifier="fixture",
            request_started_at=END,
            request_finished_at=END,
            range_start=self.run.range_start,
            range_end=self.run.range_end,
            parser_version=self.source.parser_version,
            http_status=200,
            parsed_count=0,
            stop_reason="completed",
        )
        from apps.inspection.news import inspect_news_collection

        inspection = inspect_news_collection(self.run)
        self.assertEqual(inspection.source_collection_run, self.run)
        self.assertEqual(inspection.quality_status, "failed")
        self.assertTrue(inspection.reasons)
