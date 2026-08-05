from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from django.test import SimpleTestCase, override_settings

from apps.market_data.binance import BinanceClientError, BinanceKlineClient


def milliseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def kline_row(
    open_time: datetime,
    *,
    close: str = "1238.123456789012345678",
    duration: timedelta = timedelta(hours=1),
):
    close_time = open_time + duration - timedelta(milliseconds=1)
    return [
        milliseconds(open_time),
        "1234.123456789012345678",
        "1240.000000000000000001",
        "1230.000000000000000001",
        close,
        "100.123456789012345678",
        milliseconds(close_time),
        "123456.123456789012345678",
        42,
        "55.123456789012345678",
        "67890.123456789012345678",
        "0",
    ]


class BinanceKlineClientTests(SimpleTestCase):
    range_start = datetime(2024, 1, 1, tzinfo=UTC)
    range_end = datetime(2024, 1, 2, tzinfo=UTC)
    fixed_now = datetime(2024, 2, 1, tzinfo=UTC)

    def make_client(self, handler, **kwargs):
        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http_client.close)
        return BinanceKlineClient(
            base_url="https://binance.test",
            http_client=http_client,
            sleep_fn=lambda _: None,
            now_provider=lambda: self.fixed_now,
            **kwargs,
        )

    def test_single_page_response_is_parsed_without_float(self):
        def handler(request):
            self.assertEqual(request.url.path, "/fapi/v1/klines")
            self.assertEqual(request.url.params["symbol"], "ETHUSDT")
            return httpx.Response(200, json=[kline_row(self.range_start)])

        client = self.make_client(handler)

        batches = list(
            client.iter_batches(
                symbol="ETHUSDT",
                interval="1h",
                range_start=self.range_start,
                range_end=self.range_end,
            )
        )

        self.assertEqual(client.request_count, 1)
        self.assertEqual(client.received_count, 1)
        self.assertEqual(batches[0][0].open, Decimal("1234.123456789012345678"))

    def test_five_minute_interval_is_supported(self):
        client = self.make_client(
            lambda _: httpx.Response(
                200,
                json=[kline_row(self.range_start, duration=timedelta(minutes=5))],
            )
        )

        batches = list(
            client.iter_batches(
                symbol="ETHUSDT",
                interval="5m",
                range_start=self.range_start,
                range_end=self.range_start + timedelta(minutes=5),
            )
        )

        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(
            batches[0][0].close_time,
            self.range_start + timedelta(minutes=5, milliseconds=-1),
        )

    def test_multiple_pages_are_requested_until_short_page(self):
        rows = [kline_row(self.range_start + timedelta(hours=index)) for index in range(3)]

        def handler(request):
            start_time = int(request.url.params["startTime"])
            if start_time == milliseconds(self.range_start):
                return httpx.Response(200, json=rows[:2])
            return httpx.Response(200, json=rows[2:])

        client = self.make_client(handler, page_limit=2)

        batches = list(
            client.iter_batches(
                symbol="ETHUSDT",
                interval="1h",
                range_start=self.range_start,
                range_end=self.range_end,
            )
        )

        self.assertEqual(client.request_count, 2)
        self.assertEqual(client.received_count, 3)
        self.assertEqual(sum(len(batch) for batch in batches), 3)

    def test_unclosed_kline_is_skipped(self):
        client = self.make_client(lambda _: httpx.Response(200, json=[kline_row(self.range_start)]))
        client.now_provider = lambda: self.range_start + timedelta(minutes=30)

        batches = list(
            client.iter_batches(
                symbol="ETHUSDT",
                interval="1h",
                range_start=self.range_start,
                range_end=self.range_end,
            )
        )

        self.assertEqual(batches, [[]])
        self.assertEqual(client.skipped_count, 1)

    def test_out_of_range_kline_is_skipped(self):
        outside = self.range_start - timedelta(hours=1)
        client = self.make_client(
            lambda _: httpx.Response(
                200,
                json=[kline_row(outside), kline_row(self.range_start)],
            )
        )

        batches = list(
            client.iter_batches(
                symbol="ETHUSDT",
                interval="1h",
                range_start=self.range_start,
                range_end=self.range_end,
            )
        )

        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(client.skipped_count, 1)

    def test_429_is_retried_a_limited_number_of_times(self):
        calls = 0

        def handler(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, json={"code": -1003, "msg": "rate limited"})
            return httpx.Response(200, json=[])

        client = self.make_client(handler, max_retries=2)

        list(
            client.iter_batches(
                symbol="ETHUSDT",
                interval="1h",
                range_start=self.range_start,
                range_end=self.range_end,
            )
        )

        self.assertEqual(calls, 2)
        self.assertEqual(client.request_count, 2)

    def test_parameter_4xx_is_not_retried_or_exposed_as_html(self):
        calls = 0

        def handler(_):
            nonlocal calls
            calls += 1
            return httpx.Response(
                400,
                text="<html>large unsafe error</html>",
                headers={"content-type": "text/html"},
            )

        client = self.make_client(handler, max_retries=2)

        with self.assertRaisesRegex(BinanceClientError, "response body omitted"):
            list(
                client.iter_batches(
                    symbol="ETHUSDT",
                    interval="1h",
                    range_start=self.range_start,
                    range_end=self.range_end,
                )
            )

        self.assertEqual(calls, 1)

    @override_settings(BINANCE_FUTURES_BASE_URL="https://override.example")
    def test_base_url_comes_from_settings(self):
        client = BinanceKlineClient(http_client=httpx.Client())
        self.addCleanup(client.http_client.close)

        self.assertEqual(client.base_url, "https://override.example")
