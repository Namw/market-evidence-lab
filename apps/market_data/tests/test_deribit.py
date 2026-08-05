from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from django.test import SimpleTestCase

from apps.market_data.deribit import DeribitClientError, DeribitPublicClient


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def milliseconds(value):
    return int((value - EPOCH) / timedelta(milliseconds=1))


class DeribitPublicClientTests(SimpleTestCase):
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(hours=3)

    def make_client(self, handler, **kwargs):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return DeribitPublicClient(
            base_url="https://deribit.test/api/v2",
            http_client=http,
            sleep_fn=lambda _: None,
            **kwargs,
        )

    def test_dvol_paginates_and_keeps_range_end_exclusive(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "data": [
                                [milliseconds(self.start + timedelta(hours=1)), 60, 63, 59, 62],
                                [milliseconds(self.start + timedelta(hours=2)), 62, 64, 61, 63],
                                [milliseconds(self.end), 63, 65, 62, 64],
                            ],
                            "continuation": milliseconds(self.start + timedelta(hours=1)),
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [[milliseconds(self.start), 58, 61, 57, 60]],
                        "continuation": None,
                    }
                },
            )

        client = self.make_client(handler)
        batches = list(
            client.iter_dvol_batches(
                currency="ETH",
                resolution="1h",
                range_start=self.start,
                range_end=self.end,
            )
        )

        self.assertEqual(client.request_count, 2)
        self.assertEqual(client.received_count, 4)
        self.assertEqual(client.skipped_count, 1)
        self.assertEqual(sum(map(len, batches)), 3)
        self.assertEqual(batches[0][0].open, Decimal("60"))
        self.assertEqual(requests[0].url.params["resolution"], "3600")

    def test_instruments_preserve_contract_metadata(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v2/public/get_instruments")
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "instrument_id": 123,
                            "instrument_name": "ETH-28AUG26-4000-C",
                            "base_currency": "ETH",
                            "quote_currency": "ETH",
                            "settlement_currency": "ETH",
                            "option_type": "call",
                            "strike": 4000,
                            "expiration_timestamp": milliseconds(self.end),
                            "creation_timestamp": milliseconds(self.start),
                            "contract_size": 1,
                            "is_active": True,
                            "state": "open",
                        }
                    ]
                },
            )

        item = self.make_client(handler).fetch_option_instruments()[0]

        self.assertEqual(item.instrument_id, 123)
        self.assertEqual(item.strike, Decimal("4000"))
        self.assertEqual(item.option_type, "call")

    def test_summaries_include_iv_and_option_open_interest(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "instrument_name": "ETH-28AUG26-4000-C",
                            "creation_timestamp": milliseconds(self.start),
                            "underlying_price": 3900.25,
                            "mark_price": 0.052,
                            "mark_iv": 68.125,
                            "bid_price": 0.05,
                            "ask_price": 0.054,
                            "mid_price": 0.052,
                            "last": None,
                            "open_interest": 1250.5,
                            "volume": 42.25,
                            "volume_usd": 164775,
                            "interest_rate": 0.01,
                        }
                    ]
                },
            )

        item = self.make_client(handler).fetch_option_summaries()[0]

        self.assertEqual(item.mark_iv, Decimal("68.125"))
        self.assertEqual(item.open_interest, Decimal("1250.5"))
        self.assertIsNone(item.last_price)

    def test_api_error_does_not_expose_remote_message(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"error": {"code": 10001, "message": "sensitive upstream detail"}},
            )

        client = self.make_client(handler)
        with self.assertRaisesMessage(DeribitClientError, "code=10001") as caught:
            client.fetch_option_summaries()
        self.assertNotIn("sensitive", str(caught.exception))
