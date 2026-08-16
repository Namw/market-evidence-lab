from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

DECIMAL_QUANTUM = Decimal("0.000000000000000001")


class DepthPayloadError(ValueError):
    """Raised when a Binance depth message cannot form a usable snapshot."""


class KlinePayloadError(ValueError):
    """Raised when a Binance one-minute kline message is unusable."""


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
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()


@dataclass(frozen=True, slots=True)
class MinuteKline:
    symbol: str
    event_time: datetime
    minute_start: datetime
    minute_end: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    quote_volume: Decimal
    taker_buy_quote: Decimal
    taker_sell_quote: Decimal
    delta_quote: Decimal
    trade_count: int
    first_trade_id: int | None
    last_trade_id: int | None
    closed: bool


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
        bids=tuple(bids),
        asks=tuple(asks),
    )


def parse_kline_message(payload: Mapping[str, Any]) -> MinuteKline:
    if not isinstance(payload, Mapping):
        raise KlinePayloadError("Binance returned an invalid kline payload.")
    data = payload.get("data", payload)
    if not isinstance(data, Mapping) or data.get("e") != "kline":
        raise KlinePayloadError("Binance returned an invalid kline payload.")
    kline = data.get("k")
    if not isinstance(kline, Mapping) or kline.get("i") != "1m":
        raise KlinePayloadError("Binance payload is not a one-minute kline.")
    try:
        symbol = str(data["s"]).upper()
        event_time = datetime.fromtimestamp(int(data["E"]) / 1_000, tz=UTC)
        minute_start = datetime.fromtimestamp(int(kline["t"]) / 1_000, tz=UTC)
        minute_end = datetime.fromtimestamp((int(kline["T"]) + 1) / 1_000, tz=UTC)
        open_price = decimal_18(Decimal(str(kline["o"])))
        high_price = decimal_18(Decimal(str(kline["h"])))
        low_price = decimal_18(Decimal(str(kline["l"])))
        close_price = decimal_18(Decimal(str(kline["c"])))
        quote_volume = decimal_18(Decimal(str(kline["q"])))
        taker_buy_quote = decimal_18(Decimal(str(kline["Q"])))
        trade_count = int(kline["n"])
        first_trade_id = int(kline["f"]) if trade_count else None
        last_trade_id = int(kline["L"]) if trade_count else None
        closed = bool(kline["x"])
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise KlinePayloadError("Binance kline payload is missing data.") from exc
    if min(open_price, high_price, low_price, close_price) <= 0:
        raise KlinePayloadError("Binance returned an invalid kline price.")
    if quote_volume < 0 or taker_buy_quote < 0 or taker_buy_quote > quote_volume:
        raise KlinePayloadError("Binance returned an invalid kline volume.")
    taker_sell_quote = decimal_18(quote_volume - taker_buy_quote)
    return MinuteKline(
        symbol=symbol,
        event_time=event_time,
        minute_start=minute_start,
        minute_end=minute_end,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        quote_volume=quote_volume,
        taker_buy_quote=taker_buy_quote,
        taker_sell_quote=taker_sell_quote,
        delta_quote=decimal_18(taker_buy_quote - taker_sell_quote),
        trade_count=trade_count,
        first_trade_id=first_trade_id,
        last_trade_id=last_trade_id,
        closed=closed,
    )


def parse_rest_kline(
    row: Sequence[Any],
    *,
    symbol: str,
    observed_at: datetime,
) -> MinuteKline:
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if not isinstance(row, (list, tuple)) or len(row) < 11:
        raise KlinePayloadError("Binance returned an invalid REST kline.")
    try:
        minute_start = datetime.fromtimestamp(int(row[0]) / 1_000, tz=UTC)
        minute_end = datetime.fromtimestamp((int(row[6]) + 1) / 1_000, tz=UTC)
        open_price = decimal_18(Decimal(str(row[1])))
        high_price = decimal_18(Decimal(str(row[2])))
        low_price = decimal_18(Decimal(str(row[3])))
        close_price = decimal_18(Decimal(str(row[4])))
        quote_volume = decimal_18(Decimal(str(row[7])))
        trade_count = int(row[8])
        taker_buy_quote = decimal_18(Decimal(str(row[10])))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KlinePayloadError("Binance REST kline is missing data.") from exc
    if min(open_price, high_price, low_price, close_price) <= 0:
        raise KlinePayloadError("Binance returned an invalid REST kline price.")
    if quote_volume < 0 or taker_buy_quote < 0 or taker_buy_quote > quote_volume:
        raise KlinePayloadError("Binance returned an invalid REST kline volume.")
    taker_sell_quote = decimal_18(quote_volume - taker_buy_quote)
    return MinuteKline(
        symbol=symbol.upper(),
        event_time=observed_at.astimezone(UTC),
        minute_start=minute_start,
        minute_end=minute_end,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        quote_volume=quote_volume,
        taker_buy_quote=taker_buy_quote,
        taker_sell_quote=taker_sell_quote,
        delta_quote=decimal_18(taker_buy_quote - taker_sell_quote),
        trade_count=trade_count,
        first_trade_id=None,
        last_trade_id=None,
        closed=minute_end <= observed_at.astimezone(UTC),
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
