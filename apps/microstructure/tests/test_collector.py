import asyncio
import json
from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase

from apps.microstructure.collector import OrderBookCollector, next_reconnect_delay

from .test_calculations import depth_payload, kline_payload

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def collector(**overrides) -> OrderBookCollector:
    values = {
        "symbol": "ETHUSDT",
        "ws_base_url": "wss://example.test/public/ws",
        "update_speed": "500ms",
        "sample_interval_seconds": 1,
        "reconnect_initial_seconds": 1,
        "reconnect_max_seconds": 8,
        "open_timeout_seconds": 5,
        "now_provider": lambda: NOW,
    }
    values.update(overrides)
    return OrderBookCollector(**values)


class OrderBookCollectorTests(IsolatedAsyncioTestCase):
    def test_stream_url_combines_one_minute_kline_and_top20_depth(self):
        self.assertEqual(
            collector().stream_url,
            "wss://example.test/public/stream?streams=ethusdt@kline_1m/ethusdt@depth20@500ms",
        )

    def test_valid_message_replaces_latest_book(self):
        instance = collector()

        accepted = instance.accept_message(json.dumps(depth_payload()))

        self.assertTrue(accepted)
        self.assertEqual(instance.received_messages, 1)
        self.assertEqual(instance.latest.update_id, 120)

    def test_kline_message_is_kept_for_minute_persistence(self):
        instance = collector()

        accepted = instance.accept_message(json.dumps(kline_payload()))

        self.assertTrue(accepted)
        self.assertEqual(instance.received_messages, 1)
        self.assertEqual(instance.latest_kline.trade_count, 11)
        self.assertIn(instance.latest_kline.minute_start, instance.pending_klines)

    def test_wrong_symbol_message_is_ignored(self):
        instance = collector()
        payload = depth_payload()
        payload["s"] = "BTCUSDT"

        self.assertFalse(instance.accept_message(json.dumps(payload)))
        self.assertIsNone(instance.latest)

    async def test_connection_failure_waits_before_reconnect(self):
        delays: list[float] = []
        stop_event = asyncio.Event()

        def failed_connect(*args, **kwargs):
            raise RuntimeError("offline")

        async def stop_after_first_wait(event, seconds):
            delays.append(seconds)
            event.set()
            return True

        instance = collector(
            connect_factory=failed_connect,
            wait_for_stop_fn=stop_after_first_wait,
        )

        await instance._receive(stop_event)

        self.assertEqual(delays, [1])
        self.assertEqual(instance.reconnect_count, 1)

    def test_reconnect_delay_is_exponential_and_capped(self):
        self.assertEqual(next_reconnect_delay(1, 8), 2)
        self.assertEqual(next_reconnect_delay(4, 8), 8)
        self.assertEqual(next_reconnect_delay(8, 8), 8)
