from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from django.conf import settings
from django.utils import timezone
from websockets.asyncio.client import connect

from .calculations import (
    DepthPayloadError,
    KlinePayloadError,
    MinuteKline,
    OrderBookFeatures,
    parse_depth_message,
    parse_kline_message,
    parse_rest_kline,
)
from .services import save_book_sample, save_kline

logger = logging.getLogger(__name__)


def next_reconnect_delay(current: float, maximum: float) -> float:
    if current <= 0 or maximum <= 0:
        raise ValueError("Reconnect delays must be positive.")
    return min(current * 2, maximum)


async def wait_for_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True


class OrderBookCollector:
    """Collect 1m exchange klines and Top20 depth over one combined socket."""

    def __init__(
        self,
        *,
        symbol: str,
        ws_base_url: str,
        update_speed: str,
        sample_interval_seconds: float,
        reconnect_initial_seconds: float,
        reconnect_max_seconds: float,
        open_timeout_seconds: float,
        http_base_url: str = "https://fapi.binance.com",
        kline_poll_seconds: float = 5,
        proxy_url: str = "",
        connect_factory: Callable[..., Any] = connect,
        now_provider: Callable[[], datetime] = timezone.now,
        wait_for_stop_fn: Callable[[asyncio.Event, float], Awaitable[bool]] = wait_for_stop,
    ) -> None:
        if update_speed not in {"100ms", "250ms", "500ms"}:
            raise ValueError("update_speed must be 100ms, 250ms, or 500ms")
        if sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be positive")
        if reconnect_initial_seconds <= 0 or reconnect_max_seconds <= 0:
            raise ValueError("Reconnect delays must be positive")
        if kline_poll_seconds <= 0:
            raise ValueError("kline_poll_seconds must be positive")
        self.symbol = symbol.upper()
        self.ws_base_url = ws_base_url.rstrip("/")
        self.update_speed = update_speed
        self.sample_interval_seconds = sample_interval_seconds
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.open_timeout_seconds = open_timeout_seconds
        self.http_base_url = http_base_url.rstrip("/")
        self.kline_poll_seconds = kline_poll_seconds
        self.proxy_url = proxy_url
        self.connect_factory = connect_factory
        self.now_provider = now_provider
        self.wait_for_stop_fn = wait_for_stop_fn

        self.latest: OrderBookFeatures | None = None
        self.latest_kline: MinuteKline | None = None
        self.pending_klines: dict[datetime, MinuteKline] = {}
        self.persisted_closed_minutes: set[datetime] = set()
        self.received_messages = 0
        self.saved_minute_updates = 0
        self.reconnect_count = 0
        self.latest_sampled_at: datetime | None = None
        self.connection_state = "connecting"
        self.last_error = ""

    @classmethod
    def from_settings(cls, *, symbol: str | None = None) -> OrderBookCollector:
        return cls(
            symbol=symbol or settings.MICROSTRUCTURE_SYMBOL,
            ws_base_url=settings.MICROSTRUCTURE_WS_BASE_URL,
            update_speed=settings.MICROSTRUCTURE_WS_UPDATE_SPEED,
            sample_interval_seconds=settings.MICROSTRUCTURE_SAMPLE_INTERVAL_SECONDS,
            reconnect_initial_seconds=settings.MICROSTRUCTURE_RECONNECT_INITIAL_SECONDS,
            reconnect_max_seconds=settings.MICROSTRUCTURE_RECONNECT_MAX_SECONDS,
            open_timeout_seconds=settings.MICROSTRUCTURE_WS_OPEN_TIMEOUT_SECONDS,
            http_base_url=settings.BINANCE_FUTURES_BASE_URL,
            kline_poll_seconds=settings.MICROSTRUCTURE_KLINE_POLL_SECONDS,
            proxy_url=settings.MICROSTRUCTURE_WS_PROXY_URL,
        )

    @property
    def stream_url(self) -> str:
        base = self.ws_base_url
        if base.endswith("/ws"):
            base = f"{base[:-3]}/stream"
        elif not base.endswith("/stream"):
            base = f"{base}/stream"
        symbol = self.symbol.lower()
        streams = f"{symbol}@kline_1m/{symbol}@depth20@{self.update_speed}"
        return f"{base}?streams={streams}"

    def accept_message(self, raw_message: str | bytes) -> bool:
        try:
            payload = json.loads(raw_message)
            data = payload.get("data", payload) if isinstance(payload, dict) else None
            event_type = data.get("e") if isinstance(data, dict) else None
            if event_type == "kline":
                kline = parse_kline_message(payload)
                if kline.symbol != self.symbol:
                    return False
                self.latest_kline = kline
                self.pending_klines[kline.minute_start] = kline
            elif event_type == "depthUpdate":
                features = parse_depth_message(payload, received_at=self.now_provider())
                if features.symbol != self.symbol:
                    return False
                self.latest = features
            else:
                return False
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            DepthPayloadError,
            KlinePayloadError,
        ) as exc:
            logger.warning("Ignoring unusable Binance market message: %s", exc)
            return False
        self.received_messages += 1
        return True

    async def _receive(self, stop_event: asyncio.Event) -> None:
        reconnect_delay = self.reconnect_initial_seconds
        while not stop_event.is_set():
            received_on_connection = False
            try:
                async with self.connect_factory(
                    self.stream_url,
                    open_timeout=self.open_timeout_seconds,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    proxy=self.proxy_url or None,
                ) as websocket:
                    self.connection_state = "connected"
                    self.last_error = ""
                    async for raw_message in websocket:
                        if stop_event.is_set():
                            break
                        if self.accept_message(raw_message):
                            received_on_connection = True
                            reconnect_delay = self.reconnect_initial_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if stop_event.is_set():
                    break
                self.reconnect_count += 1
                self.connection_state = "reconnecting"
                self.last_error = exc.__class__.__name__
                logger.warning(
                    "Binance market WebSocket disconnected (%s); reconnecting in %.1fs.",
                    exc.__class__.__name__,
                    reconnect_delay,
                )
                if await self.wait_for_stop_fn(stop_event, reconnect_delay):
                    break
                reconnect_delay = next_reconnect_delay(
                    reconnect_delay, self.reconnect_max_seconds
                )
            else:
                if stop_event.is_set():
                    break
                self.reconnect_count += 1
                self.connection_state = "reconnecting"
                delay = (
                    self.reconnect_initial_seconds
                    if received_on_connection
                    else reconnect_delay
                )
                if await self.wait_for_stop_fn(stop_event, delay):
                    break
                reconnect_delay = next_reconnect_delay(delay, self.reconnect_max_seconds)
        self.connection_state = "disconnected"

    async def _poll_klines(self, stop_event: asyncio.Event) -> None:
        async with httpx.AsyncClient(
            proxy=self.proxy_url or None,
            timeout=httpx.Timeout(10),
        ) as client:
            while not stop_event.is_set():
                observed_at = self.now_provider().astimezone(UTC)
                try:
                    response = await client.get(
                        f"{self.http_base_url}/fapi/v1/klines",
                        params={"symbol": self.symbol, "interval": "1m", "limit": 2},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, list):
                        raise KlinePayloadError("Binance returned an invalid REST response.")
                    for row in payload:
                        kline = parse_rest_kline(
                            row, symbol=self.symbol, observed_at=observed_at
                        )
                        self.latest_kline = kline
                        if kline.closed and kline.minute_start in self.persisted_closed_minutes:
                            continue
                        self.pending_klines[kline.minute_start] = kline
                except (httpx.HTTPError, ValueError, KlinePayloadError) as exc:
                    logger.warning("Unable to refresh Binance minute kline: %s", exc)
                if await self.wait_for_stop_fn(stop_event, self.kline_poll_seconds):
                    break

    async def sample_latest(self, *, sampled_at: datetime) -> bool:
        saved = False
        pending = list(self.pending_klines.items())
        for minute_start, kline in pending:
            await asyncio.to_thread(save_kline, kline)
            if self.pending_klines.get(minute_start) is kline:
                self.pending_klines.pop(minute_start, None)
            if kline.closed:
                self.persisted_closed_minutes.add(minute_start)
            self.saved_minute_updates += 1
            saved = True
        latest = self.latest
        if latest is not None:
            _, written = await asyncio.to_thread(
                save_book_sample, latest, sampled_at=sampled_at
            )
            if written:
                self.saved_minute_updates += 1
                saved = True
        if saved:
            self.latest_sampled_at = sampled_at
        return saved

    async def _sample(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            now = self.now_provider().astimezone(UTC)
            elapsed = now.timestamp() % self.sample_interval_seconds
            delay = self.sample_interval_seconds - elapsed
            if delay <= 0.001:
                delay = self.sample_interval_seconds
            if await self.wait_for_stop_fn(stop_event, delay):
                break
            sampled_at = self.now_provider().astimezone(UTC).replace(microsecond=0)
            await self.sample_latest(sampled_at=sampled_at)

    async def run(self, stop_event: asyncio.Event) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._receive(stop_event))
            tasks.create_task(self._sample(stop_event))
            tasks.create_task(self._poll_klines(stop_event))
