from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from apps.meme_monitor.domain import TokenMarketSnapshot


class MarketDataSourceError(RuntimeError):
    """The remote market source could not serve a usable response."""


class MemeMarketDataSource(Protocol):
    def begin_cycle(self) -> None: ...

    def drain_warnings(self) -> list[str]: ...

    def discover_new_pairs(
        self,
        *,
        observed_at: datetime,
        max_age_hours: float,
        max_pages: int,
    ) -> list[TokenMarketSnapshot]: ...

    def fetch_market_snapshots(
        self,
        pair_addresses: Sequence[str],
        *,
        observed_at: datetime,
    ) -> list[TokenMarketSnapshot]: ...

    def close(self) -> None: ...
