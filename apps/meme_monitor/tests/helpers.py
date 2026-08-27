from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.meme_monitor.domain import TokenMarketSnapshot


def make_snapshot(**overrides) -> TokenMarketSnapshot:
    observed_at = overrides.pop("timestamp", datetime(2026, 8, 27, 12, tzinfo=UTC))
    values = {
        "chain": "BSC",
        "dex": "pancakeswap_v2",
        "token_address": "0xtoken",
        "pair_address": "0xpair",
        "symbol": "MEME",
        "name": "Meme Token",
        "pair_created_at": observed_at - timedelta(minutes=43),
        "price_usd": Decimal("0.00001"),
        "liquidity_usd": Decimal(31000),
        "market_cap": None,
        "fdv": Decimal(100000),
        "volume_5m": Decimal(42000),
        "volume_1h": Decimal(100000),
        "buys_5m": 186,
        "sells_5m": 74,
        "price_change_5m": Decimal(38),
        "price_change_1h": Decimal(170),
        "timestamp": observed_at,
    }
    values.update(overrides)
    return TokenMarketSnapshot(**values)
