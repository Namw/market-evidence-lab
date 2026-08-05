from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Callable, Iterator, Sequence

import httpx
from django.conf import settings
from django.utils import timezone


SUPPORTED_CURRENCY = "ETH"
SUPPORTED_DVOL_RESOLUTIONS = {"1h": "3600"}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class DeribitClientError(RuntimeError):
    """A safe, user-displayable error raised by the Deribit client."""


@dataclass(frozen=True, slots=True)
class DvolCandlePayload:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class OptionInstrumentPayload:
    instrument_id: int
    instrument_name: str
    base_currency: str
    quote_currency: str
    settlement_currency: str
    option_type: str
    strike: Decimal
    expiration_time: datetime
    creation_time: datetime
    contract_size: Decimal
    is_active: bool
    state: str


@dataclass(frozen=True, slots=True)
class OptionSummaryPayload:
    instrument_name: str
    source_timestamp: datetime
    underlying_price: Decimal | None
    mark_price: Decimal | None
    mark_iv: Decimal | None
    bid_price: Decimal | None
    ask_price: Decimal | None
    mid_price: Decimal | None
    last_price: Decimal | None
    open_interest: Decimal | None
    volume_24h: Decimal | None
    volume_usd_24h: Decimal | None
    interest_rate: Decimal | None


def _milliseconds_to_datetime(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def _datetime_to_milliseconds(value: datetime) -> int:
    if timezone.is_naive(value):
        raise ValueError("Deribit range datetimes must be timezone-aware.")
    delta = value.astimezone(UTC) - EPOCH
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _decimal(value: object, *, optional: bool = False) -> Decimal | None:
    if value in (None, "") and optional:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DeribitClientError("Deribit returned an invalid numeric value.") from exc
    if not parsed.is_finite():
        raise DeribitClientError("Deribit returned a non-finite numeric value.")
    return parsed


class DeribitPublicClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.base_url = (base_url or settings.DERIBIT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)
        self.request_count = 0
        self.received_count = 0
        self.skipped_count = 0

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def _request(self, method: str, params: dict[str, int | str]) -> object:
        url = f"{self.base_url}/{method}"
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                response = self.http_client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
                raise DeribitClientError(
                    f"Deribit network request failed: {exc.__class__.__name__}"
                ) from exc
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    self.sleep_fn(0.25 * (2**attempt))
                    continue
            if response.status_code >= 400:
                raise DeribitClientError(
                    f"Deribit request failed with HTTP {response.status_code}."
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise DeribitClientError("Deribit returned invalid JSON.") from exc
            if not isinstance(payload, dict):
                raise DeribitClientError("Deribit returned an unexpected response shape.")
            error = payload.get("error")
            if error:
                code = error.get("code") if isinstance(error, dict) else None
                suffix = f" code={code}" if code is not None else ""
                raise DeribitClientError(f"Deribit rejected the request.{suffix}")
            if "result" not in payload:
                raise DeribitClientError("Deribit response did not include a result.")
            return payload["result"]
        raise DeribitClientError("Deribit request failed after limited retries.")

    def iter_dvol_batches(
        self,
        *,
        currency: str,
        resolution: str,
        range_start: datetime,
        range_end: datetime,
    ) -> Iterator[list[DvolCandlePayload]]:
        if currency != SUPPORTED_CURRENCY:
            raise ValueError(f"Unsupported Deribit currency: {currency}")
        if resolution not in SUPPORTED_DVOL_RESOLUTIONS:
            raise ValueError(f"Unsupported DVOL resolution: {resolution}")
        if range_start >= range_end:
            raise ValueError("range_start must be earlier than range_end")
        start_ms = _datetime_to_milliseconds(range_start)
        cursor_end_ms = _datetime_to_milliseconds(range_end)
        seen_times: set[int] = set()
        step = timedelta(hours=1)

        while cursor_end_ms > start_ms:
            result = self._request(
                "public/get_volatility_index_data",
                {
                    "currency": currency,
                    "start_timestamp": start_ms,
                    "end_timestamp": cursor_end_ms,
                    "resolution": SUPPORTED_DVOL_RESOLUTIONS[resolution],
                },
            )
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise DeribitClientError("Deribit returned invalid DVOL data.")
            rows: Sequence[object] = result["data"]
            self.received_count += len(rows)
            batch: list[DvolCandlePayload] = []
            for row in rows:
                if not isinstance(row, (list, tuple)) or len(row) < 5:
                    raise DeribitClientError("Deribit returned an invalid DVOL row.")
                try:
                    timestamp_ms = int(row[0])
                except (TypeError, ValueError) as exc:
                    raise DeribitClientError(
                        "Deribit returned an invalid DVOL timestamp."
                    ) from exc
                if (
                    timestamp_ms < start_ms
                    or timestamp_ms >= _datetime_to_milliseconds(range_end)
                    or timestamp_ms in seen_times
                ):
                    self.skipped_count += 1
                    continue
                seen_times.add(timestamp_ms)
                open_time = _milliseconds_to_datetime(timestamp_ms)
                batch.append(
                    DvolCandlePayload(
                        open_time=open_time,
                        close_time=open_time + step,
                        open=_decimal(row[1]),
                        high=_decimal(row[2]),
                        low=_decimal(row[3]),
                        close=_decimal(row[4]),
                    )
                )
            yield batch
            continuation = result.get("continuation")
            if continuation is None:
                break
            try:
                next_end_ms = int(continuation)
            except (TypeError, ValueError) as exc:
                raise DeribitClientError(
                    "Deribit returned an invalid DVOL continuation."
                ) from exc
            if next_end_ms >= cursor_end_ms:
                raise DeribitClientError("Deribit DVOL pagination stopped making progress.")
            cursor_end_ms = next_end_ms

    def fetch_option_instruments(
        self, *, currency: str = SUPPORTED_CURRENCY
    ) -> list[OptionInstrumentPayload]:
        if currency != SUPPORTED_CURRENCY:
            raise ValueError(f"Unsupported Deribit currency: {currency}")
        result = self._request(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": "false"},
        )
        if not isinstance(result, list):
            raise DeribitClientError("Deribit returned invalid option instruments.")
        self.received_count += len(result)
        payloads = []
        for row in result:
            if not isinstance(row, dict):
                raise DeribitClientError("Deribit returned an invalid instrument row.")
            try:
                payloads.append(
                    OptionInstrumentPayload(
                        instrument_id=int(row["instrument_id"]),
                        instrument_name=str(row["instrument_name"]),
                        base_currency=str(row["base_currency"]),
                        quote_currency=str(row["quote_currency"]),
                        settlement_currency=str(row["settlement_currency"]),
                        option_type=str(row["option_type"]),
                        strike=_decimal(row["strike"]),
                        expiration_time=_milliseconds_to_datetime(
                            int(row["expiration_timestamp"])
                        ),
                        creation_time=_milliseconds_to_datetime(
                            int(row["creation_timestamp"])
                        ),
                        contract_size=_decimal(row["contract_size"]),
                        is_active=bool(row["is_active"]),
                        state=str(row.get("state") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise DeribitClientError(
                    "Deribit returned an incomplete instrument row."
                ) from exc
        return payloads

    def fetch_option_summaries(
        self, *, currency: str = SUPPORTED_CURRENCY
    ) -> list[OptionSummaryPayload]:
        if currency != SUPPORTED_CURRENCY:
            raise ValueError(f"Unsupported Deribit currency: {currency}")
        result = self._request(
            "public/get_book_summary_by_currency",
            {"currency": currency, "kind": "option"},
        )
        if not isinstance(result, list):
            raise DeribitClientError("Deribit returned invalid option summaries.")
        self.received_count += len(result)
        payloads = []
        for row in result:
            if not isinstance(row, dict):
                raise DeribitClientError("Deribit returned an invalid summary row.")
            try:
                payloads.append(
                    OptionSummaryPayload(
                        instrument_name=str(row["instrument_name"]),
                        source_timestamp=_milliseconds_to_datetime(
                            int(row["creation_timestamp"])
                        ),
                        underlying_price=_decimal(
                            row.get("underlying_price"), optional=True
                        ),
                        mark_price=_decimal(row.get("mark_price"), optional=True),
                        mark_iv=_decimal(row.get("mark_iv"), optional=True),
                        bid_price=_decimal(row.get("bid_price"), optional=True),
                        ask_price=_decimal(row.get("ask_price"), optional=True),
                        mid_price=_decimal(row.get("mid_price"), optional=True),
                        last_price=_decimal(row.get("last"), optional=True),
                        open_interest=_decimal(
                            row.get("open_interest"), optional=True
                        ),
                        volume_24h=_decimal(row.get("volume"), optional=True),
                        volume_usd_24h=_decimal(
                            row.get("volume_usd"), optional=True
                        ),
                        interest_rate=_decimal(
                            row.get("interest_rate"), optional=True
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise DeribitClientError(
                    "Deribit returned an incomplete option summary."
                ) from exc
        return payloads
