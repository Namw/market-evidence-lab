from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone
from websockets.asyncio.client import connect

from .calculations import (
    DepthPayloadError,
    OrderBookFeatures,
    floor_time,
    parse_depth_message,
)
from .services import aggregate_interval, save_snapshot

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
        self.symbol = symbol.upper()
        self.ws_base_url = ws_base_url.rstrip("/")
        self.update_speed = update_speed
        self.sample_interval_seconds = sample_interval_seconds
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.open_timeout_seconds = open_timeout_seconds
        self.proxy_url = proxy_url
        self.connect_factory = connect_factory
        self.now_provider = now_provider
        self.wait_for_stop_fn = wait_for_stop_fn

        self.latest: OrderBookFeatures | None = None
        self.last_saved_update_id: int | None = None
        self.last_aggregation_boundary: datetime | None = None
        self.received_messages = 0
        self.saved_snapshots = 0
        self.reconnect_count = 0

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
            proxy_url=settings.MICROSTRUCTURE_WS_PROXY_URL,
        )

    @property
    def stream_url(self) -> str:
        stream = f"{self.symbol.lower()}@depth20@{self.update_speed}"
        return f"{self.ws_base_url}/{stream}"

    def accept_message(self, raw_message: str | bytes) -> bool:
        try:
            payload = json.loads(raw_message)
            features = parse_depth_message(payload, received_at=self.now_provider())
        except (json.JSONDecodeError, UnicodeDecodeError, DepthPayloadError) as exc:
            logger.warning("Ignoring unusable Binance depth message: %s", exc)
            return False
        if features.symbol != self.symbol:
            logger.warning(
                "Ignoring depth message for unexpected symbol %s.",
                features.symbol,
            )
            return False
        self.latest = features
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
                logger.warning(
                    "Binance depth WebSocket disconnected (%s); reconnecting in %.1fs.",
                    exc.__class__.__name__,
                    reconnect_delay,
                )
                if await self.wait_for_stop_fn(stop_event, reconnect_delay):
                    break
                reconnect_delay = next_reconnect_delay(
                    reconnect_delay,
                    self.reconnect_max_seconds,
                )
            else:
                if stop_event.is_set():
                    break
                self.reconnect_count += 1
                delay = (
                    self.reconnect_initial_seconds
                    if received_on_connection
                    else reconnect_delay
                )
                if await self.wait_for_stop_fn(stop_event, delay):
                    break
                reconnect_delay = next_reconnect_delay(delay, self.reconnect_max_seconds)

    async def sample_latest(self, *, sampled_at: datetime) -> bool:
        latest = self.latest
        if latest is None or latest.update_id == self.last_saved_update_id:
            return False
        await asyncio.to_thread(save_snapshot, latest, sampled_at=sampled_at)
        self.last_saved_update_id = latest.update_id
        self.saved_snapshots += 1

        boundary = floor_time(sampled_at, seconds=300)
        if boundary != self.last_aggregation_boundary:
            await asyncio.to_thread(
                aggregate_interval,
                symbol=self.symbol,
                interval_start=boundary - timedelta(minutes=5),
            )
            self.last_aggregation_boundary = boundary
        return True

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
