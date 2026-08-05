from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Callable, Iterator

import httpx
from django.conf import settings
from django.utils import timezone

from apps.collection.source_network import source_proxy_url

from .binance import (
    SUPPORTED_SYMBOL,
    BinanceClientError,
    _datetime_to_milliseconds,
    _milliseconds_to_datetime,
)


@dataclass(frozen=True, slots=True)
class OpenInterestPayload:
    timestamp: datetime
    sum_open_interest: Decimal
    sum_open_interest_value: Decimal


@dataclass(frozen=True, slots=True)
class FundingRatePayload:
    funding_time: datetime
    funding_rate: Decimal
    mark_price: Decimal | None
    rate_type: str


class _BinanceDerivativesClient:
    endpoint = ""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        page_limit: int = 500,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.base_url = (base_url or settings.BINANCE_FUTURES_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.page_limit = page_limit
        self.sleep_fn = sleep_fn
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
        if "json" in response.headers.get("content-type", "").lower():
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                code = payload.get("code")
                message = " ".join(str(payload.get("msg", "request rejected")).split())
                return (f"code={code}, message={message}" if code is not None else message)[:300]
        return "response body omitted"

    def _request_page(self, params: dict[str, int | str]) -> list[dict[str, object]]:
        url = f"{self.base_url}{self.endpoint}"
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                response = self.http_client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
                raise BinanceClientError(
                    f"Binance network request failed: {exc.__class__.__name__}"
                ) from exc
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
            if response.status_code >= 400:
                raise BinanceClientError(
                    f"Binance request failed with HTTP {response.status_code}: "
                    f"{self._error_detail(response)}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise BinanceClientError("Binance returned invalid JSON.") from exc
            if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                raise BinanceClientError("Binance returned an unexpected response shape.")
            self.received_count += len(payload)
            return payload
        raise BinanceClientError("Binance request failed after limited retries.")

    def _iter_rows(
        self,
        *,
        symbol: str,
        range_start: datetime,
        range_end: datetime,
        extra_params: dict[str, str] | None = None,
        time_key: str,
        newest_first: bool = False,
    ) -> Iterator[list[dict[str, object]]]:
        if symbol != SUPPORTED_SYMBOL:
            raise ValueError(f"Unsupported symbol: {symbol}")
        if range_start >= range_end:
            raise ValueError("range_start must be earlier than range_end")
        if timezone.is_naive(range_start) or timezone.is_naive(range_end):
            raise ValueError("range datetimes must be timezone-aware")
        start_ms = _datetime_to_milliseconds(range_start)
        end_ms = _datetime_to_milliseconds(range_end)
        cursor_ms = start_ms
        cursor_end_ms = end_ms - 1
        seen_times: set[int] = set()
        while cursor_ms < end_ms and cursor_end_ms >= start_ms:
            params: dict[str, int | str] = {
                "symbol": symbol,
                "startTime": start_ms if newest_first else cursor_ms,
                "endTime": cursor_end_ms if newest_first else end_ms - 1,
                "limit": self.page_limit,
            }
            if extra_params:
                params.update(extra_params)
            rows = self._request_page(params)
            if not rows:
                break
            batch: list[dict[str, object]] = []
            max_time_ms: int | None = None
            min_time_ms: int | None = None
            for row in rows:
                try:
                    time_ms = int(row[time_key])
                except (KeyError, TypeError, ValueError) as exc:
                    raise BinanceClientError("Binance returned an invalid timestamp.") from exc
                max_time_ms = time_ms if max_time_ms is None else max(max_time_ms, time_ms)
                min_time_ms = time_ms if min_time_ms is None else min(min_time_ms, time_ms)
                if time_ms < start_ms or time_ms >= end_ms or time_ms in seen_times:
                    self.skipped_count += 1
                    continue
                seen_times.add(time_ms)
                batch.append(row)
            yield batch
            if newest_first:
                if min_time_ms is None:
                    raise BinanceClientError("Binance pagination returned no timestamp.")
                next_end_ms = min_time_ms - 1
                if next_end_ms >= cursor_end_ms:
                    if len(rows) < self.page_limit and not batch:
                        break
                    raise BinanceClientError("Binance pagination stopped making progress.")
                cursor_end_ms = next_end_ms
            else:
                if max_time_ms is None:
                    raise BinanceClientError("Binance pagination returned no timestamp.")
                next_cursor_ms = max_time_ms + 1
                if next_cursor_ms <= cursor_ms:
                    if len(rows) < self.page_limit and not batch:
                        break
                    raise BinanceClientError("Binance pagination stopped making progress.")
                cursor_ms = next_cursor_ms
            if len(rows) < self.page_limit:
                break


class BinanceOpenInterestClient(_BinanceDerivativesClient):
    endpoint = "/futures/data/openInterestHist"
    supported_periods = {"1h", "5m"}

    def iter_batches(
        self,
        *,
        symbol: str,
        period: str,
        range_start: datetime,
        range_end: datetime,
    ) -> Iterator[list[OpenInterestPayload]]:
        if period not in self.supported_periods:
            raise ValueError(f"Unsupported OI period: {period}")
        for rows in self._iter_rows(
            symbol=symbol,
            range_start=range_start,
            range_end=range_end,
            extra_params={"period": period},
            time_key="timestamp",
            newest_first=True,
        ):
            batch = []
            for row in rows:
                try:
                    batch.append(
                        OpenInterestPayload(
                            timestamp=_milliseconds_to_datetime(int(row["timestamp"])),
                            sum_open_interest=Decimal(str(row["sumOpenInterest"])),
                            sum_open_interest_value=Decimal(str(row["sumOpenInterestValue"])),
                        )
                    )
                except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
                    raise BinanceClientError("Binance returned an invalid OI value.") from exc
            yield batch


class BinanceFundingRateClient(_BinanceDerivativesClient):
    endpoint = "/fapi/v1/fundingRate"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("page_limit", 1_000)
        super().__init__(**kwargs)

    def iter_batches(
        self,
        *,
        symbol: str,
        range_start: datetime,
        range_end: datetime,
    ) -> Iterator[list[FundingRatePayload]]:
        for rows in self._iter_rows(
            symbol=symbol,
            range_start=range_start,
            range_end=range_end,
            time_key="fundingTime",
        ):
            batch = []
            for row in rows:
                try:
                    mark_price_raw = row.get("markPrice")
                    batch.append(
                        FundingRatePayload(
                            funding_time=_milliseconds_to_datetime(int(row["fundingTime"])),
                            funding_rate=Decimal(str(row["fundingRate"])),
                            mark_price=(
                                None
                                if mark_price_raw in (None, "")
                                else Decimal(str(mark_price_raw))
                            ),
                            rate_type=str(row.get("rateType") or "")[:40],
                        )
                    )
                except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
                    raise BinanceClientError("Binance returned an invalid funding value.") from exc
            yield batch
