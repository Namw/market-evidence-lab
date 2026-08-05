from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .models import AddressBalanceDaily, EtfFlowDaily, StablecoinSupplyDaily


def _stablecoin_at_or_before(target_date):
    return (
        StablecoinSupplyDaily.objects.filter(
            chain="Ethereum", stablecoin_symbol="", observation_date__lte=target_date
        )
        .order_by("-observation_date")
        .first()
    )


def stablecoin_metrics():
    latest = _stablecoin_at_or_before("9999-12-31")
    if latest is None:
        return {"latest": None, "change_1d": None, "change_7d": None, "change_30d": None, "trend": []}
    result = {"latest": latest}
    for days in (1, 7, 30):
        previous = _stablecoin_at_or_before(latest.observation_date - timedelta(days=days))
        result[f"change_{days}d"] = (
            latest.circulating_supply_usd - previous.circulating_supply_usd
            if previous
            else None
        )
    result["trend"] = list(
        StablecoinSupplyDaily.objects.filter(
            chain="Ethereum",
            stablecoin_symbol="",
            observation_date__gte=latest.observation_date - timedelta(days=89),
        ).order_by("observation_date")
    )
    result["revision_detected"] = latest.updated_at > latest.created_at + timedelta(seconds=1)
    return result


def etf_metrics():
    totals = list(
        EtfFlowDaily.objects.filter(is_total=True)
        .order_by("-trade_date")[:20]
    )
    if not totals:
        return {"latest": None, "cumulative_5d": None, "cumulative_20d": None, "contributions": [], "trend": []}
    latest = totals[0]

    def cumulative(rows):
        values = [item.flow_usd for item in rows if item.flow_usd is not None]
        return sum(values, Decimal("0")) if values else None

    contributions = list(
        EtfFlowDaily.objects.filter(trade_date=latest.trade_date, is_total=False)
        .order_by("ticker")
    )
    return {
        "latest": latest,
        "cumulative_5d": cumulative(totals[:5]),
        "cumulative_20d": cumulative(totals),
        "contributions": contributions,
        "trend": list(reversed(totals)),
    }


def address_metrics(*, large_change_threshold=Decimal("10000")):
    dates = list(
        AddressBalanceDaily.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .distinct()[:8]
    )
    if not dates:
        return {"snapshot_date": None, "covered_count": 0, "rows": [], "large_change_threshold": large_change_threshold}
    current_date = dates[0]
    prior_1d = next((item for item in dates if item <= current_date - timedelta(days=1)), None)
    prior_7d = next((item for item in dates if item <= current_date - timedelta(days=7)), None)
    current = list(
        AddressBalanceDaily.objects.filter(snapshot_date=current_date)
        .select_related("address")
        .order_by("rank")
    )

    def by_address(snapshot_date):
        if snapshot_date is None:
            return {}
        return {
            item.address_id: item
            for item in AddressBalanceDaily.objects.filter(snapshot_date=snapshot_date)
        }

    one_day = by_address(prior_1d)
    seven_day = by_address(prior_7d)
    rows = []
    for item in current:
        previous_1d = one_day.get(item.address_id)
        previous_7d = seven_day.get(item.address_id)
        change_1d = item.balance_eth - previous_1d.balance_eth if previous_1d else None
        change_7d = item.balance_eth - previous_7d.balance_eth if previous_7d else None
        rows.append(
            {
                "balance": item,
                "balance_change_1d": change_1d,
                "balance_change_7d": change_7d,
                "rank_change": previous_1d.rank - item.rank if previous_1d else None,
                "newly_entered_top_list": previous_1d is None,
                "large_balance_change": change_1d is not None and abs(change_1d) >= large_change_threshold,
            }
        )
    return {
        "snapshot_date": current_date,
        "covered_count": len(current),
        "rows": rows,
        "large_change_threshold": large_change_threshold,
        "block_number": next((item.block_number for item in current if item.block_number), None),
        "observed_at": max((item.observed_at for item in current), default=None),
        "label_updated_at": max((item.address.updated_at for item in current), default=None),
    }


def svg_polyline(items, value_getter, *, width=720, height=180):
    values = [Decimal(value_getter(item)) for item in items if value_getter(item) is not None]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    spread = high - low or Decimal("1")
    points = []
    for index, value in enumerate(values):
        x = Decimal(index) * Decimal(width) / Decimal(len(values) - 1)
        y = Decimal(height) - ((value - low) / spread * Decimal(height - 12)) - Decimal("6")
        points.append(f"{float(x):.1f},{float(y):.1f}")
    return " ".join(points)
