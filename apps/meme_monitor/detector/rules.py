from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.meme_monitor.domain import MemeAnomalyEvent, TokenMarketSnapshot


@dataclass(frozen=True, slots=True)
class MemeDetectorConfig:
    price_change_5m_pct: Decimal
    minimum_volume_5m_usd: Decimal
    volume_spike_multiplier: Decimal
    volume_history_min_samples: int
    minimum_transactions_5m: int
    minimum_liquidity_usd: Decimal


class MemeAnomalyDetector:
    """Small composable rule set; no data-source or persistence knowledge."""

    def __init__(self, config: MemeDetectorConfig) -> None:
        self.config = config

    def detect(
        self,
        snapshot: TokenMarketSnapshot,
        *,
        historical_volumes: Sequence[Decimal],
        event_time: datetime,
    ) -> MemeAnomalyEvent | None:
        triggered: list[str] = []

        price_passed = (
            snapshot.price_change_5m is not None
            and snapshot.price_change_5m >= self.config.price_change_5m_pct
        )
        if price_passed:
            triggered.append("price_spike")

        volume_passed = (
            snapshot.volume_5m is not None
            and snapshot.volume_5m >= self.config.minimum_volume_5m_usd
        )
        if volume_passed:
            triggered.append("volume_threshold")

        if (
            snapshot.volume_5m is not None
            and len(historical_volumes) >= self.config.volume_history_min_samples
        ):
            baseline = sum(historical_volumes, start=Decimal(0)) / Decimal(
                len(historical_volumes)
            )
            if (
                baseline > 0
                and snapshot.volume_5m / baseline >= self.config.volume_spike_multiplier
            ):
                triggered.append("volume_spike")

        transaction_count = (snapshot.buys_5m or 0) + (snapshot.sells_5m or 0)
        activity_passed = transaction_count >= self.config.minimum_transactions_5m
        if activity_passed:
            triggered.append("active_trading")

        liquidity_passed = (
            snapshot.liquidity_usd is not None
            and snapshot.liquidity_usd >= self.config.minimum_liquidity_usd
        )
        if liquidity_passed:
            triggered.append("minimum_liquidity")

        if not (
            price_passed and volume_passed and activity_passed and liquidity_passed
        ):
            return None

        return MemeAnomalyEvent(
            event_time=event_time,
            chain=snapshot.chain,
            token_address=snapshot.token_address,
            pair_address=snapshot.pair_address,
            symbol=snapshot.symbol,
            name=snapshot.name,
            pair_age_minutes=snapshot.pair_age_minutes(event_time),
            price_usd=snapshot.price_usd,
            price_change_5m=snapshot.price_change_5m,
            price_change_1h=snapshot.price_change_1h,
            volume_5m=snapshot.volume_5m,
            liquidity_usd=snapshot.liquidity_usd,
            buys_5m=snapshot.buys_5m,
            sells_5m=snapshot.sells_5m,
            triggered_rules=tuple(triggered),
        )
