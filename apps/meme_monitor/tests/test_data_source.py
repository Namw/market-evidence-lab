import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from django.test import SimpleTestCase

from apps.meme_monitor.data_source import GeckoTerminalDataSource


class GeckoTerminalDataSourceTests(SimpleTestCase):
    def test_discovers_and_normalizes_bsc_pool(self):
        payload = _pool_payload()

        def handler(request):
            self.assertEqual(request.url.path, "/networks/bsc/new_pools")
            return httpx.Response(200, content=json.dumps(payload).encode())

        source = GeckoTerminalDataSource(
            network="bsc",
            chain="BSC",
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
            min_request_interval_seconds=0,
        )
        observed_at = datetime(2026, 8, 27, 16, tzinfo=UTC)
        try:
            snapshots = source.discover_new_pairs(
                observed_at=observed_at,
                max_age_hours=24,
                max_pages=1,
            )
        finally:
            source.close()

        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot.chain, "BSC")
        self.assertEqual(snapshot.dex, "pancakeswap_v2")
        self.assertEqual(snapshot.token_address, "0xtoken")
        self.assertEqual(snapshot.name, "My Meme")
        self.assertEqual(snapshot.symbol, "MEME")
        self.assertEqual(snapshot.price_usd, Decimal("0.00001"))
        self.assertEqual(snapshot.volume_5m, Decimal(42000))
        self.assertEqual(snapshot.buys_5m, 186)
        self.assertEqual(snapshot.price_change_1h, Decimal(170))
        self.assertEqual(snapshot.launchpad_graduation_percentage, Decimal("42.5"))
        self.assertFalse(snapshot.launchpad_completed)
        self.assertEqual(snapshot.migrated_destination_pair_address, "")

    def test_skips_one_malformed_pool_without_losing_valid_pool(self):
        payload = _pool_payload()
        payload["data"].insert(0, {"id": "bad", "attributes": {}})
        source = GeckoTerminalDataSource(
            network="bsc",
            chain="BSC",
            base_url="https://example.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
            min_request_interval_seconds=0,
        )
        try:
            snapshots = source.discover_new_pairs(
                observed_at=datetime(2026, 8, 27, 16, tzinfo=UTC),
                max_age_hours=24,
                max_pages=1,
            )
        finally:
            source.close()
        self.assertEqual([item.pair_address for item in snapshots], ["0xpair"])

    def test_retries_transport_error(self):
        attempts = 0
        delays = []

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectTimeout("temporary timeout", request=request)
            return httpx.Response(200, json=_pool_payload())

        source = GeckoTerminalDataSource(
            network="bsc",
            chain="BSC",
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
            max_retries=1,
            min_request_interval_seconds=0,
            sleep=delays.append,
        )
        try:
            snapshots = source.discover_new_pairs(
                observed_at=datetime(2026, 8, 27, 16, tzinfo=UTC),
                max_age_hours=24,
                max_pages=1,
            )
        finally:
            source.close()
        self.assertEqual(attempts, 2)
        self.assertEqual(delays, [1])
        self.assertEqual(len(snapshots), 1)


def _pool_payload():
    return {
        "data": [
            {
                "id": "bsc_0xpair",
                "type": "pool",
                "attributes": {
                    "address": "0xpair",
                    "name": "MEME / WBNB",
                    "pool_created_at": "2026-08-27T15:17:00Z",
                    "base_token_price_usd": "0.00001",
                    "reserve_in_usd": "31000",
                    "market_cap_usd": None,
                    "fdv_usd": "100000",
                    "price_change_percentage": {"m5": "38", "h1": "170"},
                    "transactions": {
                        "m5": {"buys": 186, "sells": 74},
                    },
                    "volume_usd": {"m5": "42000", "h1": "100000"},
                    "launchpad_details": {
                        "graduation_percentage": 42.5,
                        "completed": False,
                        "completed_at": None,
                        "migrated_destination_pool_address": None,
                    },
                },
                "relationships": {
                    "base_token": {"data": {"id": "bsc_0xtoken"}},
                    "quote_token": {"data": {"id": "bsc_0xwbnb"}},
                    "dex": {"data": {"id": "pancakeswap_v2"}},
                },
            }
        ],
        "included": [
            {
                "id": "bsc_0xtoken",
                "type": "token",
                "attributes": {
                    "address": "0xtoken",
                    "name": "My Meme",
                    "symbol": "MEME",
                },
            }
        ],
    }
