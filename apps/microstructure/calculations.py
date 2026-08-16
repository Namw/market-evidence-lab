from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

DECIMAL_QUANTUM = Decimal("0.000000000000000001")


class DepthPayloadError(ValueError):
    """Raised when a Binance depth message cannot form a usable snapshot."""


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookFeatures:
    symbol: str
    event_time: datetime
    received_at: datetime
    update_id: int
    best_bid: Decimal
    best_ask: Decimal
    mid_price: Decimal
    spread: Decimal
    spread_bps: Decimal | None
    bid_depth_top5_quote: Decimal
    ask_depth_top5_quote: Decimal
    bid_depth_top10_quote: Decimal
    ask_depth_top10_quote: Decimal
    bid_depth_top20_quote: Decimal
    ask_depth_top20_quote: Decimal
    imbalance_top5: Decimal | None
    imbalance_top10: Decimal | None
    imbalance_top20: Decimal | None


def decimal_18(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        return value.quantize(DECIMAL_QUANTUM)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return decimal_18(numerator / denominator)


def quote_depth(levels: Sequence[DepthLevel], count: int) -> Decimal:
    return decimal_18(
        sum(
            (item.price * item.quantity for item in levels[:count]),
            Decimal(0),
        )
    )


def depth_imbalance(bid_depth: Decimal, ask_depth: Decimal) -> Decimal | None:
    return _ratio(bid_depth - ask_depth, bid_depth + ask_depth)


def _parse_levels(value: Any, *, side: str) -> list[DepthLevel]:
    if not isinstance(value, list) or not value:
        raise DepthPayloadError(f"Binance depth payload has no {side} levels.")
    levels: list[DepthLevel] = []
    try:
        for row in value[:20]:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise DepthPayloadError(f"Binance returned an invalid {side} level.")
            price = Decimal(str(row[0]))
            quantity = Decimal(str(row[1]))
            if price <= 0 or quantity < 0:
                raise DepthPayloadError(f"Binance returned an invalid {side} value.")
            levels.append(DepthLevel(price=price, quantity=quantity))
    except (InvalidOperation, TypeError, ValueError) as exc:
        if isinstance(exc, DepthPayloadError):
            raise
        raise DepthPayloadError(f"Binance returned an invalid {side} value.") from exc
    return levels


def parse_depth_message(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
) -> OrderBookFeatures:
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    if not isinstance(payload, Mapping):
        raise DepthPayloadError("Binance returned an invalid depth payload.")
    data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise DepthPayloadError("Binance returned an invalid depth payload.")
    try:
        symbol = str(data["s"]).upper()
        event_time = datetime.fromtimestamp(int(data["E"]) / 1_000, tz=UTC)
        update_id = int(data["u"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DepthPayloadError("Binance depth payload is missing metadata.") from exc

    bids = sorted(
        _parse_levels(data.get("b"), side="bid"),
        key=lambda level: level.price,
        reverse=True,
    )
    asks = sorted(
        _parse_levels(data.get("a"), side="ask"),
        key=lambda level: level.price,
    )
    best_bid = bids[0].price
    best_ask = asks[0].price
    spread = decimal_18(best_ask - best_bid)
    mid_price = decimal_18((best_bid + best_ask) / Decimal(2))
    spread_bps = _ratio(spread * Decimal(10_000), mid_price)

    depths: dict[int, tuple[Decimal, Decimal]] = {}
    imbalances: dict[int, Decimal | None] = {}
    for count in (5, 10, 20):
        bid_depth = quote_depth(bids, count)
        ask_depth = quote_depth(asks, count)
        depths[count] = (bid_depth, ask_depth)
        imbalances[count] = depth_imbalance(bid_depth, ask_depth)

    return OrderBookFeatures(
        symbol=symbol,
        event_time=event_time,
        received_at=received_at.astimezone(UTC),
        update_id=update_id,
        best_bid=decimal_18(best_bid),
        best_ask=decimal_18(best_ask),
        mid_price=mid_price,
        spread=spread,
        spread_bps=spread_bps,
        bid_depth_top5_quote=depths[5][0],
        ask_depth_top5_quote=depths[5][1],
        bid_depth_top10_quote=depths[10][0],
        ask_depth_top10_quote=depths[10][1],
        bid_depth_top20_quote=depths[20][0],
        ask_depth_top20_quote=depths[20][1],
        imbalance_top5=imbalances[5],
        imbalance_top10=imbalances[10],
        imbalance_top20=imbalances[20],
    )


def floor_time(value: datetime, *, seconds: int) -> datetime:
    if value.tzinfo is None:
        raise ValueError("value must be timezone-aware")
    utc_value = value.astimezone(UTC)
    timestamp = int(utc_value.timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % seconds, tz=UTC)


def iter_five_minute_starts(start: datetime, end: datetime) -> Iterable[datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("range datetimes must be timezone-aware")
    if start >= end:
        raise ValueError("start must be earlier than end")
    utc_start = start.astimezone(UTC)
    utc_end = end.astimezone(UTC)
    if (
        floor_time(utc_start, seconds=300) != utc_start
        or floor_time(utc_end, seconds=300) != utc_end
    ):
        raise ValueError("range datetimes must align to UTC five-minute boundaries")
    cursor = utc_start
    while cursor < utc_end:
        yield cursor
        cursor += timedelta(minutes=5)
