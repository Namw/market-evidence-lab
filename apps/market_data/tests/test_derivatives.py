from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from django.test import SimpleTestCase

from apps.market_data.derivatives import (
    BinanceFundingRateClient,
    BinanceOpenInterestClient,
)


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def milliseconds(value):
    return int((value - EPOCH) / timedelta(milliseconds=1))


class DerivativesClientTests(SimpleTestCase):
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = start + timedelta(days=1)

    def make_client(self, cls, handler, **kwargs):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return cls(
            base_url="https://binance.test",
            http_client=http,
            sleep_fn=lambda _: None,
            **kwargs,
        )

    def test_oi_pages_and_preserves_decimal_precision(self):
        rows = [
            {
                "symbol": "ETHUSDT",
                "timestamp": milliseconds(self.start + timedelta(hours=index)),
                "sumOpenInterest": f"1000.12345678901234567{index}",
                "sumOpenInterestValue": f"3000000.12345678901234567{index}",
            }
            for index in range(3)
        ]

        requested_ends = []

        def handler(request):
            self.assertEqual(request.url.path, "/futures/data/openInterestHist")
            self.assertEqual(request.url.params["period"], "1h")
            requested_ends.append(int(request.url.params["endTime"]))
            return httpx.Response(200, json=rows[1:] if len(requested_ends) == 1 else rows[:1])

        client = self.make_client(BinanceOpenInterestClient, handler, page_limit=2)
        batches = list(client.iter_batches(symbol="ETHUSDT", period="1h", range_start=self.start, range_end=self.end))

        self.assertEqual(client.request_count, 2)
        self.assertEqual(sum(map(len, batches)), 3)
        self.assertEqual(requested_ends[1], milliseconds(self.start + timedelta(hours=1)) - 1)
        self.assertEqual(batches[1][0].sum_open_interest, Decimal("1000.123456789012345670"))

    def test_oi_supports_five_minute_period(self):
        def handler(request):
            self.assertEqual(request.url.params["period"], "5m")
            return httpx.Response(
                200,
                json=[{
                    "symbol": "ETHUSDT",
                    "timestamp": milliseconds(self.start),
                    "sumOpenInterest": "1000.5",
                    "sumOpenInterestValue": "3000000.5",
                }],
            )

        client = self.make_client(BinanceOpenInterestClient, handler)
        batches = list(
            client.iter_batches(
                symbol="ETHUSDT",
                period="5m",
                range_start=self.start,
                range_end=self.start + timedelta(minutes=5),
            )
        )

        self.assertEqual(len(batches[0]), 1)

    def test_funding_inclusive_api_boundary_is_exposed_as_exclusive_range(self):
        boundary = self.start + timedelta(hours=8)
        rows = [
            {"symbol": "ETHUSDT", "fundingTime": milliseconds(self.start), "fundingRate": "0.000300000000000001", "markPrice": "3456.123456789012345678"},
            {"symbol": "ETHUSDT", "fundingTime": milliseconds(boundary), "fundingRate": "-0.000100000000000001", "markPrice": "3400.1"},
        ]
        requested_starts = []

        def handler(request):
            requested_starts.append(int(request.url.params["startTime"]))
            if len(requested_starts) == 1:
                return httpx.Response(200, json=[rows[0]])
            return httpx.Response(200, json=[rows[0], rows[1]])

        client = self.make_client(BinanceFundingRateClient, handler, page_limit=1)
        batches = list(client.iter_batches(symbol="ETHUSDT", range_start=self.start, range_end=boundary))

        self.assertEqual(requested_starts[1], milliseconds(self.start) + 1)
        self.assertEqual(sum(map(len, batches)), 1)
        self.assertEqual(client.skipped_count, 2)
        self.assertEqual(batches[0][0].mark_price, Decimal("3456.123456789012345678"))
