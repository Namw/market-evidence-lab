from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class TokenMarketSnapshot:
    chain: str
    dex: str
    token_address: str
    pair_address: str
    symbol: str
    name: str
    pair_created_at: datetime
    timestamp: datetime
    price_usd: Decimal | None = None
    liquidity_usd: Decimal | None = None
    market_cap: Decimal | None = None
    fdv: Decimal | None = None
    volume_5m: Decimal | None = None
    volume_1h: Decimal | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    price_change_5m: Decimal | None = None
    price_change_1h: Decimal | None = None
    launchpad_graduation_percentage: Decimal | None = None
    launchpad_completed: bool | None = None
    launchpad_completed_at: datetime | None = None
    migrated_destination_pair_address: str = ""

    def pair_age_minutes(self, at: datetime | None = None) -> int:
        observed_at = at or self.timestamp
        return max(0, int((observed_at - self.pair_created_at).total_seconds() // 60))


@dataclass(frozen=True, slots=True)
class MemeAnomalyEvent:
    event_time: datetime
    chain: str
    token_address: str
    pair_address: str
    symbol: str
    name: str
    pair_age_minutes: int
    price_usd: Decimal | None
    price_change_5m: Decimal | None
    price_change_1h: Decimal | None
    volume_5m: Decimal | None
    liquidity_usd: Decimal | None
    buys_5m: int | None
    sells_5m: int | None
    triggered_rules: tuple[str, ...]
    anomaly_type: str = "market_spike"
    event_id: UUID = field(default_factory=uuid4)
