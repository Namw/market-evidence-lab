from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from django.db import transaction

from .calculations import OrderBookFeatures, decimal_18, iter_five_minute_starts
from .models import OrderBookFiveMinuteSummary, OrderBookSnapshot


def save_snapshot(features: OrderBookFeatures, *, sampled_at: datetime) -> OrderBookSnapshot:
    if sampled_at.tzinfo is None:
        raise ValueError("sampled_at must be timezone-aware")
    values = {
        field: getattr(features, field)
        for field in (
            "event_time",
            "received_at",
            "update_id",
            "best_bid",
            "best_ask",
            "mid_price",
            "spread",
            "spread_bps",
            "bid_depth_top5_quote",
            "ask_depth_top5_quote",
            "bid_depth_top10_quote",
            "ask_depth_top10_quote",
            "bid_depth_top20_quote",
            "ask_depth_top20_quote",
            "imbalance_top5",
            "imbalance_top10",
            "imbalance_top20",
        )
    }
    snapshot, _ = OrderBookSnapshot.objects.update_or_create(
        symbol=features.symbol,
        sampled_at=sampled_at,
        defaults=values,
    )
    return snapshot


def _mean(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    with localcontext() as context:
        context.prec = 60
        return decimal_18(sum(present, Decimal(0)) / Decimal(len(present)))


def _summary_values(
    snapshots: Sequence[OrderBookSnapshot],
    interval_end: datetime,
) -> dict[str, object]:
    first = snapshots[0]
    last = snapshots[-1]
    spread_values = [
        item.spread_bps for item in snapshots if item.spread_bps is not None
    ]
    values: dict[str, object] = {
        "interval_end": interval_end,
        "mid_open": first.mid_price,
        "mid_high": max(item.mid_price for item in snapshots),
        "mid_low": min(item.mid_price for item in snapshots),
        "mid_close": last.mid_price,
        "spread_bps_mean": _mean(item.spread_bps for item in snapshots),
        "spread_bps_max": max(spread_values) if spread_values else None,
        "spread_bps_end": last.spread_bps,
        "snapshot_count": len(snapshots),
    }
    for depth in (5, 10, 20):
        for side in ("bid", "ask"):
            source = f"{side}_depth_top{depth}_quote"
            values[f"{source}_mean"] = _mean(
                getattr(item, source) for item in snapshots
            )
        imbalance = f"imbalance_top{depth}"
        values[f"{imbalance}_mean"] = _mean(getattr(item, imbalance) for item in snapshots)
        values[f"{imbalance}_end"] = getattr(last, imbalance)
    return values


@transaction.atomic
def aggregate_interval(
    *,
    symbol: str,
    interval_start: datetime,
) -> OrderBookFiveMinuteSummary | None:
    if interval_start.tzinfo is None:
        raise ValueError("interval_start must be timezone-aware")
    interval_end = interval_start + timedelta(minutes=5)
    snapshots = list(
        OrderBookSnapshot.objects.filter(
            symbol=symbol,
            sampled_at__gte=interval_start,
            sampled_at__lt=interval_end,
        ).order_by("sampled_at")
    )
    if not snapshots:
        return None
    summary, _ = OrderBookFiveMinuteSummary.objects.update_or_create(
        symbol=symbol,
        interval_start=interval_start,
        defaults=_summary_values(snapshots, interval_end),
    )
    return summary


def aggregate_range(
    *,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
) -> tuple[int, int]:
    written = 0
    empty = 0
    for interval_start in iter_five_minute_starts(range_start, range_end):
        if aggregate_interval(symbol=symbol, interval_start=interval_start) is None:
            empty += 1
        else:
            written += 1
    return written, empty
