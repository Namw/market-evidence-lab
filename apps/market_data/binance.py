from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Callable, Iterator, Sequence

import httpx
from django.conf import settings
from django.utils import timezone

from apps.collection.source_network import source_proxy_url


SUPPORTED_SYMBOL = "ETHUSDT"
SUPPORTED_INTERVALS = {"1d", "1h", "5m"}
INTERVAL_MILLISECONDS = {
    "1d": 86_400_000,
    "1h": 3_600_000,
    "5m": 300_000,
}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class BinanceClientError(RuntimeError):
    """A safe, user-displayable error raised by the Binance client."""


@dataclass(frozen=True, slots=True)
class KlinePayload:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal


def _milliseconds_to_datetime(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def _datetime_to_milliseconds(value: datetime) -> int:
    if timezone.is_naive(value):
        raise ValueError("Kline range datetimes must be timezone-aware.")
    delta = value.astimezone(UTC) - EPOCH
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


class BinanceKlineClient:
    endpoint = "/fapi/v1/klines"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        page_limit: int = 1_500,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = sleep,
        now_provider: Callable[[], datetime] = timezone.now,
    ) -> None:
        self.base_url = (base_url or settings.BINANCE_FUTURES_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.page_limit = page_limit
        self.sleep_fn = sleep_fn
        self.now_provider = now_provider
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=timeout_seconds,
            proxy=source_proxy_url("binance_futures") or None,
            trust_env=False,
        )
        self.request_count = 0
        self.received_count = 0
        self.skipped_count = 0

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def _error_detail(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                code = payload.get("code")
                message = str(payload.get("msg", "Binance rejected the request"))
                detail = f"code={code}, message={message}" if code is not None else message
                return detail.replace("\n", " ")[:300]
        return "response body omitted"

    def _request_page(self, params: dict[str, int | str]) -> Sequence[Sequence[object]]:
        last_network_error: httpx.RequestError | None = None
        url = f"{self.base_url}{self.endpoint}"

        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                response = self.http_client.get(url, params=params)
            except httpx.RequestError as exc:
                last_network_error = exc
                if attempt < self.max_retries:
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
                raise BinanceClientError(
                    f"Binance network request failed after {self.request_count} request(s): "
                    f"{exc.__class__.__name__}: {str(exc)[:240]}"
                ) from exc

            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
                raise BinanceClientError(
                    f"Binance request failed after limited retries with HTTP "
                    f"{response.status_code}: {self._error_detail(response)}"
                )

            if response.status_code >= 400:
                raise BinanceClientError(
                    f"Binance request failed with HTTP {response.status_code}: "
                    f"{self._error_detail(response)}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise BinanceClientError("Binance returned invalid JSON.") from exc
            if not isinstance(payload, list):
                raise BinanceClientError("Binance returned an unexpected response shape.")
            self.received_count += len(payload)
            return payload

        raise BinanceClientError(
            f"Binance network request failed: {last_network_error or 'unknown error'}"
        )

    def _parse_row(self, row: Sequence[object]) -> KlinePayload:
        if len(row) < 11:
            raise BinanceClientError("Binance returned an incomplete kline row.")
        try:
            return KlinePayload(
                open_time=_milliseconds_to_datetime(int(row[0])),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
                close_time=_milliseconds_to_datetime(int(row[6])),
                quote_volume=Decimal(str(row[7])),
                trade_count=int(row[8]),
                taker_buy_base_volume=Decimal(str(row[9])),
                taker_buy_quote_volume=Decimal(str(row[10])),
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BinanceClientError("Binance returned an invalid kline value.") from exc

    def iter_batches(
        self,
        *,
        symbol: str,
        interval: str,
        range_start: datetime,
        range_end: datetime,
    ) -> Iterator[list[KlinePayload]]:
        if symbol != SUPPORTED_SYMBOL:
            raise ValueError(f"Unsupported symbol: {symbol}")
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}")
        if range_start >= range_end:
            raise ValueError("range_start must be earlier than range_end")

        start_ms = _datetime_to_milliseconds(range_start)
        end_ms = _datetime_to_milliseconds(range_end)
        cursor_ms = start_ms
        seen_open_times: set[int] = set()
        now = self.now_provider().astimezone(UTC)

        while cursor_ms < end_ms:
            rows = self._request_page(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor_ms,
                    "endTime": end_ms - 1,
                    "limit": self.page_limit,
                }
            )
            if not rows:
                break

            batch: list[KlinePayload] = []
            max_open_ms: int | None = None
            for row in rows:
                if not isinstance(row, (list, tuple)) or not row:
                    raise BinanceClientError("Binance returned an invalid kline row shape.")
                try:
                    open_ms = int(row[0])
                except (TypeError, ValueError) as exc:
                    raise BinanceClientError("Binance returned an invalid open time.") from exc
                max_open_ms = open_ms if max_open_ms is None else max(max_open_ms, open_ms)

                if open_ms < start_ms or open_ms >= end_ms:
                    self.skipped_count += 1
                    continue
                payload = self._parse_row(row)
                if payload.close_time >= now:
                    self.skipped_count += 1
                    continue
                if open_ms in seen_open_times:
                    self.skipped_count += 1
                    continue
                seen_open_times.add(open_ms)
                batch.append(payload)

            yield batch

            if max_open_ms is None:
                raise BinanceClientError("Binance pagination returned no usable open time.")
            next_cursor_ms = max_open_ms + INTERVAL_MILLISECONDS[interval]
            if next_cursor_ms <= cursor_ms:
                raise BinanceClientError("Binance pagination stopped making progress.")
            cursor_ms = next_cursor_ms
            if len(rows) < self.page_limit:
                break
