from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from django.conf import settings
from django.db import transaction

from .calculations import MinuteKline, OrderBookFeatures, decimal_18, floor_time
from .models import MarketMinute
from .research import FUTURE_HORIZON_MINUTES, refresh_future_5m_returns


def _mean(total: Decimal, count: int) -> Decimal | None:
    if count <= 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return decimal_18(total / Decimal(count))


def _p95(values: list[str]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(Decimal(value) for value in values)
    return decimal_18(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)])


@transaction.atomic
def save_kline(kline: MinuteKline) -> MarketMinute:
    minute, _ = MarketMinute.objects.select_for_update().get_or_create(
        symbol=kline.symbol,
        minute_start=kline.minute_start,
        defaults={"minute_end": kline.minute_end},
    )
    minute.minute_end = kline.minute_end
    minute.open_price = kline.open_price
    minute.high_price = kline.high_price
    minute.low_price = kline.low_price
    minute.close_price = kline.close_price
    minute.quote_volume = kline.quote_volume
    minute.taker_buy_quote = kline.taker_buy_quote
    minute.taker_sell_quote = kline.taker_sell_quote
    minute.delta_quote = kline.delta_quote
    minute.trade_count = kline.trade_count
    minute.first_trade_id = kline.first_trade_id
    minute.last_trade_id = kline.last_trade_id
    minute.kline_closed = kline.closed
    minute.latest_event_time = kline.event_time
    minute.save()
    refresh_future_5m_returns(
        symbol=kline.symbol,
        candidate_start=kline.minute_start
        - timedelta(minutes=FUTURE_HORIZON_MINUTES),
        candidate_end=kline.minute_start,
    )
    return minute


@transaction.atomic
def save_book_sample(
    features: OrderBookFeatures,
    *,
    sampled_at: datetime,
) -> tuple[MarketMinute, bool]:
    if sampled_at.tzinfo is None:
        raise ValueError("sampled_at must be timezone-aware")
    minute_start = floor_time(sampled_at, seconds=60)
    minute, _ = MarketMinute.objects.select_for_update().get_or_create(
        symbol=features.symbol,
        minute_start=minute_start,
        defaults={"minute_end": minute_start + timedelta(minutes=1)},
    )
    if minute.last_book_sample_at and minute.last_book_sample_at >= sampled_at:
        return minute, False

    bid = features.bid_depth_top20_quote
    ask = features.ask_depth_top20_quote
    spread = features.spread_bps
    if minute.book_sample_count == 0:
        minute.bid_depth_open = bid
        minute.ask_depth_open = ask
        minute.first_book_sample_at = sampled_at
    minute.bid_depth_close = bid
    minute.ask_depth_close = ask
    minute.bid_depth_sum += bid
    minute.ask_depth_sum += ask
    minute.book_sample_count += 1
    minute.bid_depth_mean = _mean(minute.bid_depth_sum, minute.book_sample_count)
    minute.ask_depth_mean = _mean(minute.ask_depth_sum, minute.book_sample_count)
    if features.imbalance_top5 is not None:
        minute.imbalance_top5_close = features.imbalance_top5
        minute.imbalance_top5_sum += features.imbalance_top5
        minute.imbalance_top5_sample_count += 1
        minute.imbalance_top5_mean = _mean(
            minute.imbalance_top5_sum,
            minute.imbalance_top5_sample_count,
        )
    if spread is not None:
        samples = [*minute.spread_bps_samples, str(spread)]
        minute.spread_bps_samples = samples
        minute.spread_bps_sum += spread
        minute.spread_bps_mean = _mean(minute.spread_bps_sum, len(samples))
        minute.spread_bps_p95 = _p95(samples)
    expected = max(1, round(60 / settings.MICROSTRUCTURE_SAMPLE_INTERVAL_SECONDS))
    minute.coverage_ratio = decimal_18(
        min(Decimal(1), Decimal(minute.book_sample_count) / Decimal(expected))
    )
    minute.last_book_sample_at = sampled_at
    if minute.latest_event_time is None or features.event_time > minute.latest_event_time:
        minute.latest_event_time = features.event_time
    minute.save()
    return minute, True
