from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Sum

from .models import (
    DeribitOptionInstrument,
    DeribitOptionMarketSnapshot,
    DeribitVolatilityIndexCandle,
)


ZERO = Decimal("0")


def format_decimal(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{places}f}"


def format_compact(value: Decimal | None) -> str:
    if value is None:
        return "—"
    absolute = abs(value)
    for divisor, suffix in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if absolute >= divisor:
            return f"{value / divisor:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:,.0f}"


def _sum(values) -> Decimal:
    return sum((value for value in values if value is not None), ZERO)


def _ratio(put_oi: Decimal, call_oi: Decimal) -> Decimal | None:
    return put_oi / call_oi if call_oi else None


def _metric_change(
    current: Decimal | None,
    previous: Decimal | None,
    *,
    compact_delta: bool = False,
    ratio_delta: bool = False,
) -> dict:
    if current is None or previous is None:
        return {
            "available": False,
            "direction": "",
            "change": "历史不足 24h",
            "detail": "继续每日采集后自动显示",
        }
    delta = current - previous
    direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
    arrow = "▲" if delta > 0 else "▼" if delta < 0 else "—"
    if ratio_delta:
        change = f"{arrow} {abs(delta):.2f}" if delta else "— 0.00"
        detail = f"24h {previous:.2f} → {current:.2f}"
    else:
        percentage = delta / abs(previous) * Decimal("100") if previous else None
        change = (
            f"{arrow} {abs(percentage):.1f}%"
            if percentage is not None and delta
            else "— 0.0%"
        )
        delta_text = format_compact(abs(delta)) if compact_delta else f"{abs(delta):.2f}"
        sign = "+" if delta > 0 else "−" if delta < 0 else ""
        detail = f"24h {sign}{delta_text}"
    return {
        "available": True,
        "direction": direction,
        "change": change,
        "detail": detail,
    }


def _snapshot_totals(observed_at: datetime | None) -> dict:
    if observed_at is None:
        return {"total": ZERO, "call": ZERO, "put": ZERO}
    queryset = DeribitOptionMarketSnapshot.objects.filter(observed_at=observed_at)
    total = queryset.aggregate(value=Sum("open_interest"))["value"] or ZERO
    call = queryset.filter(
        instrument__option_type=DeribitOptionInstrument.OptionType.CALL
    ).aggregate(value=Sum("open_interest"))["value"] or ZERO
    put = queryset.filter(
        instrument__option_type=DeribitOptionInstrument.OptionType.PUT
    ).aggregate(value=Sum("open_interest"))["value"] or ZERO
    return {"total": total, "call": call, "put": put}


def _interpolate_iv(rows: list, option_type: str, underlying: Decimal) -> Decimal | None:
    by_strike: dict[Decimal, list[Decimal]] = defaultdict(list)
    for row in rows:
        if row.instrument.option_type == option_type and row.mark_iv is not None:
            by_strike[row.instrument.strike].append(row.mark_iv)
    points = sorted(
        (strike, _sum(values) / len(values)) for strike, values in by_strike.items()
    )
    if not points:
        return None
    strikes = [point[0] for point in points]
    index = bisect_left(strikes, underlying)
    if index == 0:
        return points[0][1]
    if index == len(points):
        return points[-1][1]
    lower_strike, lower_iv = points[index - 1]
    upper_strike, upper_iv = points[index]
    if upper_strike == lower_strike:
        return lower_iv
    weight = (underlying - lower_strike) / (upper_strike - lower_strike)
    return lower_iv + (upper_iv - lower_iv) * weight


def _atm_iv(rows: list, underlying: Decimal | None) -> Decimal | None:
    if underlying is None:
        return None
    values = [
        value
        for value in (
            _interpolate_iv(rows, DeribitOptionInstrument.OptionType.CALL, underlying),
            _interpolate_iv(rows, DeribitOptionInstrument.OptionType.PUT, underlying),
        )
        if value is not None
    ]
    return _sum(values) / len(values) if values else None


def _chart_coordinates(values: list[Decimal], *, width: int, height: int) -> tuple:
    left, right, top, bottom = 54, width - 22, 24, height - 42
    low = min(values)
    high = max(values)
    padding = max((high - low) * Decimal("0.15"), Decimal("1"))
    low -= padding
    high += padding
    span = high - low or Decimal("1")
    x_step = (right - left) / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = left + index * x_step
        y = bottom - float((value - low) / span) * (bottom - top)
        points.append((round(x, 2), round(y, 2)))
    ticks = []
    for index in range(4):
        value = low + span * Decimal(index) / Decimal("3")
        y = bottom - index * (bottom - top) / 3
        ticks.append({"value": f"{value:.1f}%", "y": round(y, 2)})
    return points, ticks


def _term_structure(expiry_groups: list[dict]) -> dict:
    rows = [item for item in expiry_groups if item["atm_iv"] is not None]
    if not rows:
        return {"points": "", "items": [], "ticks": []}
    coordinates, ticks = _chart_coordinates(
        [item["atm_iv"] for item in rows], width=760, height=280
    )
    items = []
    for row, (x, y) in zip(rows, coordinates, strict=True):
        items.append(
            {
                "x": x,
                "y": y,
                "label": row["expiration_time"].strftime("%m-%d"),
                "value": f'{row["atm_iv"]:.2f}%',
            }
        )
    return {
        "points": " ".join(f"{item['x']},{item['y']}" for item in items),
        "items": items,
        "ticks": ticks,
    }


def _downsample(rows: list[dict], limit: int = 20) -> list[dict]:
    if len(rows) <= limit:
        return rows
    indexes = {
        round(index * (len(rows) - 1) / (limit - 1)) for index in range(limit)
    }
    return [row for index, row in enumerate(rows) if index in indexes]


def _skew_series(expiry_groups: list[dict], latest_at: datetime) -> dict:
    if not expiry_groups:
        return {"series": [], "ticks": []}
    selected = []
    used_expiries = set()
    for target_days in (30, 90):
        target = latest_at + timedelta(days=target_days)
        group = min(
            expiry_groups,
            key=lambda item: abs((item["expiration_time"] - target).total_seconds()),
        )
        if group["expiration_time"] in used_expiries:
            continue
        used_expiries.add(group["expiration_time"])
        by_strike: dict[Decimal, list[Decimal]] = defaultdict(list)
        underlying = group["underlying"]
        if not underlying:
            continue
        for row in group["rows"]:
            if row.mark_iv is not None:
                by_strike[row.instrument.strike].append(row.mark_iv)
        points = []
        for strike, ivs in sorted(by_strike.items()):
            moneyness = strike / underlying * Decimal("100")
            if Decimal("70") <= moneyness <= Decimal("130"):
                points.append(
                    {
                        "moneyness": moneyness,
                        "iv": _sum(ivs) / len(ivs),
                    }
                )
        points = _downsample(points)
        if points:
            selected.append(
                {
                    "label": f"约 {target_days} 天 · {group['expiration_time']:%m-%d}",
                    "points": points,
                }
            )
    all_values = [point["iv"] for item in selected for point in item["points"]]
    if not all_values:
        return {"series": [], "ticks": []}
    low = min(all_values)
    high = max(all_values)
    padding = max((high - low) * Decimal("0.12"), Decimal("1"))
    low -= padding
    high += padding
    span = high - low or Decimal("1")
    left, right, top, bottom = 58, 870, 24, 205
    for item in selected:
        coordinates = []
        for point in item["points"]:
            x = left + float((point["moneyness"] - Decimal("70")) / Decimal("60")) * (right - left)
            y = bottom - float((point["iv"] - low) / span) * (bottom - top)
            coordinates.append(
                {
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "x_value": f'{point["moneyness"]:.1f}%',
                    "y_value": f'{point["iv"]:.2f}%',
                }
            )
        item["coordinates"] = coordinates
        item["path"] = " ".join(
            f"{point['x']},{point['y']}" for point in coordinates
        )
    ticks = []
    for index in range(4):
        value = low + span * Decimal(index) / Decimal("3")
        y = bottom - index * (bottom - top) / 3
        ticks.append({"value": f"{value:.1f}%", "y": round(y, 2)})
    return {"series": selected, "ticks": ticks}


def build_deribit_options_context(*, requested_expiry: str = "") -> dict:
    latest_at = (
        DeribitOptionMarketSnapshot.objects.order_by("-observed_at")
        .values_list("observed_at", flat=True)
        .first()
    )
    if latest_at is None:
        return {"has_data": False}

    snapshots = list(
        DeribitOptionMarketSnapshot.objects.filter(observed_at=latest_at)
        .select_related("instrument")
        .order_by("instrument__expiration_time", "instrument__strike")
    )
    latest_totals = {
        "total": _sum(row.open_interest for row in snapshots),
        "call": _sum(
            row.open_interest
            for row in snapshots
            if row.instrument.option_type == DeribitOptionInstrument.OptionType.CALL
        ),
        "put": _sum(
            row.open_interest
            for row in snapshots
            if row.instrument.option_type == DeribitOptionInstrument.OptionType.PUT
        ),
    }
    previous_at = (
        DeribitOptionMarketSnapshot.objects.filter(
            observed_at__lte=latest_at - timedelta(hours=24)
        )
        .order_by("-observed_at")
        .values_list("observed_at", flat=True)
        .first()
    )
    previous_totals = _snapshot_totals(previous_at) if previous_at else None
    current_ratio = _ratio(latest_totals["put"], latest_totals["call"])
    previous_ratio = (
        _ratio(previous_totals["put"], previous_totals["call"])
        if previous_totals
        else None
    )

    latest_dvol = DeribitVolatilityIndexCandle.objects.order_by("-open_time").first()
    previous_dvol = None
    if latest_dvol:
        previous_dvol = (
            DeribitVolatilityIndexCandle.objects.filter(
                open_time__lte=latest_dvol.open_time - timedelta(hours=24)
            )
            .order_by("-open_time")
            .first()
        )

    grouped_rows: dict[datetime, list] = defaultdict(list)
    for row in snapshots:
        grouped_rows[row.instrument.expiration_time].append(row)
    expiry_groups = []
    for expiration_time, rows in sorted(grouped_rows.items()):
        underlying_values = [
            row.underlying_price for row in rows if row.underlying_price is not None
        ]
        underlying = (
            _sum(underlying_values) / len(underlying_values)
            if underlying_values
            else None
        )
        call_oi = _sum(
            row.open_interest
            for row in rows
            if row.instrument.option_type == DeribitOptionInstrument.OptionType.CALL
        )
        put_oi = _sum(
            row.open_interest
            for row in rows
            if row.instrument.option_type == DeribitOptionInstrument.OptionType.PUT
        )
        expiry_groups.append(
            {
                "expiration_time": expiration_time,
                "key": expiration_time.date().isoformat(),
                "rows": rows,
                "underlying": underlying,
                "atm_iv": _atm_iv(rows, underlying),
                "call_oi": call_oi,
                "put_oi": put_oi,
                "total_oi": call_oi + put_oi,
            }
        )

    max_total = max((group["total_oi"] for group in expiry_groups), default=ZERO)
    max_call = max((group["call_oi"] for group in expiry_groups), default=ZERO)
    max_put = max((group["put_oi"] for group in expiry_groups), default=ZERO)
    for group in expiry_groups:
        group.update(
            {
                "total_display": format_compact(group["total_oi"]),
                "call_display": format_compact(group["call_oi"]),
                "put_display": format_compact(group["put_oi"]),
                "both_call_width": round(float(group["call_oi"] / max_total * 100), 2) if max_total else 0,
                "both_put_width": round(float(group["put_oi"] / max_total * 100), 2) if max_total else 0,
                "call_width": round(float(group["call_oi"] / max_call * 100), 2) if max_call else 0,
                "put_width": round(float(group["put_oi"] / max_put * 100), 2) if max_put else 0,
            }
        )

    selected_group = next(
        (group for group in expiry_groups if group["key"] == requested_expiry),
        None,
    )
    if selected_group is None and expiry_groups:
        selected_group = max(expiry_groups, key=lambda item: item["total_oi"])

    strike_rows = []
    if selected_group:
        by_strike = defaultdict(lambda: {"call": ZERO, "put": ZERO})
        for row in selected_group["rows"]:
            if row.open_interest is None:
                continue
            key = "call" if row.instrument.option_type == DeribitOptionInstrument.OptionType.CALL else "put"
            by_strike[row.instrument.strike][key] += row.open_interest
        strike_max = max(
            (max(values["call"], values["put"]) for values in by_strike.values()),
            default=ZERO,
        )
        for strike, values in sorted(by_strike.items()):
            if not values["call"] and not values["put"]:
                continue
            strike_rows.append(
                {
                    "strike": format_decimal(strike, 0),
                    "call": format_compact(values["call"]),
                    "put": format_compact(values["put"]),
                    "call_height": round(float(values["call"] / strike_max * 100), 2) if strike_max else 0,
                    "put_height": round(float(values["put"] / strike_max * 100), 2) if strike_max else 0,
                    "call_raw": values["call"],
                    "put_raw": values["put"],
                }
            )
        max_call_strike = max(strike_rows, key=lambda item: item["call_raw"], default=None)
        max_put_strike = max(strike_rows, key=lambda item: item["put_raw"], default=None)
    else:
        max_call_strike = max_put_strike = None

    underlying_values = [
        row.underlying_price for row in snapshots if row.underlying_price is not None
    ]
    underlying = (
        _sum(underlying_values) / len(underlying_values)
        if underlying_values
        else None
    )
    atm_30_group = min(
        expiry_groups,
        key=lambda item: abs(
            (item["expiration_time"] - (latest_at + timedelta(days=30))).total_seconds()
        ),
        default=None,
    )

    return {
        "has_data": True,
        "latest_at": latest_at,
        "previous_at": previous_at,
        "active_instrument_count": len(snapshots),
        "underlying": format_decimal(underlying),
        "dvol": {
            "value": format_decimal(latest_dvol.close) if latest_dvol else "—",
            **_metric_change(
                latest_dvol.close if latest_dvol else None,
                previous_dvol.close if previous_dvol else None,
            ),
        },
        "total_oi": {
            "value": format_compact(latest_totals["total"]),
            **_metric_change(
                latest_totals["total"],
                previous_totals["total"] if previous_totals else None,
                compact_delta=True,
            ),
        },
        "put_call": {
            "value": format_decimal(current_ratio) if current_ratio is not None else "—",
            "put": format_compact(latest_totals["put"]),
            "call": format_compact(latest_totals["call"]),
            **_metric_change(current_ratio, previous_ratio, ratio_delta=True),
        },
        "atm_30": {
            "value": f'{atm_30_group["atm_iv"]:.2f}%' if atm_30_group and atm_30_group["atm_iv"] is not None else "—",
            "expiry": atm_30_group["expiration_time"] if atm_30_group else None,
        },
        "expiry_groups": expiry_groups,
        "term_structure": _term_structure(expiry_groups),
        "selected_expiry": selected_group,
        "strike_rows": strike_rows,
        "max_call_strike": max_call_strike,
        "max_put_strike": max_put_strike,
        "selected_ratio": _ratio(selected_group["put_oi"], selected_group["call_oi"]) if selected_group else None,
        "skew": _skew_series(expiry_groups, latest_at),
    }
