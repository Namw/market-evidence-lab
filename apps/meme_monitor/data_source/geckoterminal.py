from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Self

import httpx

from apps.meme_monitor.data_source.base import MarketDataSourceError
from apps.meme_monitor.domain import TokenMarketSnapshot

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GeckoTerminalDataSource:
    """GeckoTerminal public API adapter for one configured network."""

    def __init__(
        self,
        *,
        network: str,
        chain: str,
        base_url: str = "https://api.geckoterminal.com/api/v2",
        timeout_seconds: float = 15,
        max_retries: int = 2,
        min_request_interval_seconds: float = 2.1,
        proxy_url: str = "",
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.network = network
        self.chain = chain
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, min(max_retries, 5))
        self.min_request_interval_seconds = max(0, min_request_interval_seconds)
        self._sleep = sleep
        self._last_request_at: float | None = None
        self._cycle_warnings: list[str] = []
        self._client = httpx.Client(
            headers={
                "Accept": "application/json;version=20230302",
                "User-Agent": "MarketEvidenceLab/1.0 meme-monitor",
            },
            timeout=timeout_seconds,
            follow_redirects=True,
            proxy=proxy_url or None,
            trust_env=False,
            transport=transport,
        )

    def begin_cycle(self) -> None:
        self._cycle_warnings.clear()

    def drain_warnings(self) -> list[str]:
        warnings = list(self._cycle_warnings)
        self._cycle_warnings.clear()
        return warnings

    def discover_new_pairs(
        self,
        *,
        observed_at: datetime,
        max_age_hours: float,
        max_pages: int,
    ) -> list[TokenMarketSnapshot]:
        cutoff = observed_at - timedelta(hours=max_age_hours)
        snapshots: list[TokenMarketSnapshot] = []
        seen_pairs: set[str] = set()
        for page in range(1, max(1, min(max_pages, 10)) + 1):
            try:
                payload = self._get_json(
                    f"/networks/{self.network}/new_pools",
                    params={
                        "include": "base_token,quote_token",
                        "page": page,
                    },
                )
            except MarketDataSourceError:
                if page == 1:
                    raise
                self._warn_cycle(
                    "new-pool discovery page %s failed; keeping earlier pages", page
                )
                break
            page_snapshots = self._parse_payload(payload, observed_at=observed_at)
            if not page_snapshots:
                break
            for snapshot in page_snapshots:
                if snapshot.pair_created_at < cutoff:
                    continue
                key = snapshot.pair_address
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    snapshots.append(snapshot)
            if min(item.pair_created_at for item in page_snapshots) < cutoff:
                break
        return snapshots

    def fetch_market_snapshots(
        self,
        pair_addresses: Sequence[str],
        *,
        observed_at: datetime,
    ) -> list[TokenMarketSnapshot]:
        snapshots: list[TokenMarketSnapshot] = []
        unique_addresses = list(dict.fromkeys(pair_addresses))
        for start in range(0, len(unique_addresses), 30):
            batch = unique_addresses[start : start + 30]
            addresses = ",".join(batch)
            try:
                payload = self._get_json(
                    f"/networks/{self.network}/pools/multi/{addresses}",
                    params={"include": "base_token,quote_token"},
                )
            except MarketDataSourceError as exc:
                self._warn_cycle(
                    "market request failed for batch of %s pairs: %s",
                    len(batch),
                    exc,
                )
                continue
            snapshots.extend(self._parse_payload(payload, observed_at=observed_at))
        return snapshots

    def _get_json(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            self._respect_rate_limit()
            try:
                response = self._client.get(f"{self.base_url}{path}", params=params)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise MarketDataSourceError(str(exc)) from exc
                delay = min(2**attempt, 5)
                self._warn_cycle("request failed, retrying in %ss: %s", delay, exc)
                self._sleep(delay)
                continue
            if response.status_code not in RETRYABLE_STATUS_CODES:
                try:
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPStatusError, ValueError) as exc:
                    raise MarketDataSourceError(str(exc)) from exc
                if not isinstance(payload, dict):
                    raise MarketDataSourceError("unexpected non-object response")
                return payload
            if attempt >= self.max_retries:
                raise MarketDataSourceError(
                    f"HTTP {response.status_code} after {attempt + 1} attempts"
                )
            retry_after = response.headers.get("Retry-After")
            try:
                delay = (
                    min(float(retry_after), 10) if retry_after else min(2**attempt, 5)
                )
            except ValueError:
                delay = min(2**attempt, 5)
            self._warn_cycle(
                "request returned HTTP %s, retrying in %ss",
                response.status_code,
                delay,
            )
            self._sleep(delay)
        raise MarketDataSourceError("bounded retry loop exhausted")

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self.min_request_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = time.monotonic()

    def _parse_payload(
        self,
        payload: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> list[TokenMarketSnapshot]:
        included = {
            item.get("id"): item.get("attributes", {})
            for item in (payload.get("included") or [])
            if isinstance(item, dict) and item.get("type") == "token"
        }
        snapshots: list[TokenMarketSnapshot] = []
        for pool in payload.get("data") or []:
            try:
                snapshot = self._parse_pool(
                    pool,
                    included=included,
                    observed_at=observed_at,
                )
            except (KeyError, TypeError, ValueError) as exc:
                pool_id = (
                    pool.get("id", "unknown") if isinstance(pool, dict) else "unknown"
                )
                self._warn_cycle("skipping malformed pool %s: %s", pool_id, exc)
                continue
            snapshots.append(snapshot)
        return snapshots

    def _parse_pool(
        self,
        pool: dict[str, Any],
        *,
        included: dict[str, dict[str, Any]],
        observed_at: datetime,
    ) -> TokenMarketSnapshot:
        attributes = pool["attributes"]
        relationships = pool["relationships"]
        base_token_id = relationships["base_token"]["data"]["id"]
        base_token = included.get(base_token_id, {})
        token_address = base_token.get("address") or base_token_id.removeprefix(
            f"{self.network}_"
        )
        dex = relationships.get("dex", {}).get("data", {}).get("id", "unknown")
        transactions = attributes.get("transactions") or {}
        volume = attributes.get("volume_usd") or {}
        price_change = attributes.get("price_change_percentage") or {}
        txns_5m = transactions.get("m5") or {}
        created_at = _parse_datetime(attributes["pool_created_at"])
        launchpad = attributes.get("launchpad_details")
        if not isinstance(launchpad, dict):
            launchpad = None
        return TokenMarketSnapshot(
            chain=self.chain,
            dex=str(dex),
            token_address=str(token_address),
            pair_address=str(attributes["address"]),
            symbol=str(base_token.get("symbol") or ""),
            name=str(base_token.get("name") or ""),
            pair_created_at=created_at,
            price_usd=_decimal_or_none(attributes.get("base_token_price_usd")),
            liquidity_usd=_decimal_or_none(attributes.get("reserve_in_usd")),
            market_cap=_decimal_or_none(attributes.get("market_cap_usd")),
            fdv=_decimal_or_none(attributes.get("fdv_usd")),
            volume_5m=_decimal_or_none(volume.get("m5")),
            volume_1h=_decimal_or_none(volume.get("h1")),
            buys_5m=_int_or_none(txns_5m.get("buys")),
            sells_5m=_int_or_none(txns_5m.get("sells")),
            price_change_5m=_decimal_or_none(price_change.get("m5")),
            price_change_1h=_decimal_or_none(price_change.get("h1")),
            launchpad_graduation_percentage=(
                _decimal_or_none(launchpad.get("graduation_percentage"))
                if launchpad
                else None
            ),
            launchpad_completed=(
                bool(launchpad.get("completed")) if launchpad else None
            ),
            launchpad_completed_at=(
                _parse_datetime_or_none(launchpad.get("completed_at"))
                if launchpad
                else None
            ),
            migrated_destination_pair_address=(
                str(launchpad.get("migrated_destination_pool_address") or "")
                if launchpad
                else ""
            ),
            timestamp=observed_at,
        )

    def close(self) -> None:
        self._client.close()

    def _warn_cycle(self, message: str, *args: Any) -> None:
        rendered = message % args if args else message
        self._cycle_warnings.append(rendered[:500])
        logger.warning("%s", rendered)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_datetime(str(value))
    except (TypeError, ValueError):
        return None
