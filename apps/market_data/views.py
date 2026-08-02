from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import FundingRate, Kline, OpenInterest


EXCHANGE = Kline.Exchange.BINANCE
MARKET_TYPE = Kline.MarketType.USD_M_FUTURES
SYMBOL = "ETHUSDT"
DAILY_LIMIT = 60


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _kline_row(kline: Kline) -> dict[str, str]:
    return {
        "open_time": _utc_iso(kline.open_time),
        "open": str(kline.open),
        "high": str(kline.high),
        "low": str(kline.low),
        "close": str(kline.close),
        "volume": str(kline.volume),
    }


def _format_decimal(value: Decimal, places: int = 2) -> str:
    formatted = f"{value:,.{places}f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _format_compact(value: Decimal) -> str:
    absolute = abs(value)
    for divisor, suffix in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if absolute >= divisor:
            return f"{value / divisor:.1f}".rstrip("0").rstrip(".") + suffix
    return _format_decimal(value)


def _daily_detail(kline: Kline | None) -> dict[str, str]:
    if kline is None:
        return {}
    change = (
        (kline.close - kline.open) / kline.open * Decimal("100")
        if kline.open
        else Decimal("0")
    )
    return {
        "date": kline.open_time.astimezone(UTC).date().isoformat(),
        "open": _format_decimal(kline.open),
        "high": _format_decimal(kline.high),
        "low": _format_decimal(kline.low),
        "close": _format_decimal(kline.close),
        "change": f"{change:+.2f}%",
        "change_class": "is-up" if change > 0 else "is-down" if change < 0 else "",
        "volume": _format_compact(kline.volume),
    }


@require_GET
def data_view(request):
    daily_rows = list(
        Kline.objects.filter(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            interval=Kline.Interval.ONE_DAY,
        ).order_by("-open_time")[:DAILY_LIMIT]
    )
    daily_rows.reverse()

    requested_date = _parse_date(request.GET.get("date"))
    daily_by_date = {
        row.open_time.astimezone(UTC).date(): row for row in daily_rows
    }
    latest_date = (
        daily_rows[-1].open_time.astimezone(UTC).date() if daily_rows else None
    )
    selected_date = (
        requested_date if requested_date in daily_by_date else latest_date
    )
    selected_daily = daily_by_date.get(selected_date)

    hourly_rows: list[Kline] = []
    oi_rows: list[OpenInterest] = []
    funding_rows: list[FundingRate] = []
    range_start = None
    range_end = None
    if selected_date is not None:
        range_start = datetime.combine(
            selected_date - timedelta(days=1), time.min, tzinfo=UTC
        )
        # The latest closed daily candle has no following closed day yet. Historical
        # selections include the following day to give the requested three-day view.
        days_after_selection = 1 if selected_date == latest_date else 2
        range_end = datetime.combine(
            selected_date + timedelta(days=days_after_selection), time.min, tzinfo=UTC
        )
        common_filters = {
            "exchange": EXCHANGE,
            "market_type": MARKET_TYPE,
            "symbol": SYMBOL,
        }
        hourly_rows = list(
            Kline.objects.filter(
                **common_filters,
                interval=Kline.Interval.ONE_HOUR,
                open_time__gte=range_start,
                open_time__lt=range_end,
            ).order_by("open_time")
        )
        oi_rows = list(
            OpenInterest.objects.filter(
                **common_filters,
                period="1h",
                timestamp__gte=range_start,
                timestamp__lt=range_end,
            ).order_by("timestamp")
        )
        funding_rows = list(
            FundingRate.objects.filter(
                **common_filters,
                funding_time__gte=range_start,
                funding_time__lt=range_end,
            ).order_by("funding_time")
        )

    daily_dates = list(daily_by_date)
    selected_index = daily_dates.index(selected_date) if selected_date in daily_dates else -1
    previous_date = daily_dates[selected_index - 1] if selected_index > 0 else None
    next_date = (
        daily_dates[selected_index + 1]
        if 0 <= selected_index < len(daily_dates) - 1
        else None
    )

    selected_day_start = (
        datetime.combine(selected_date, time.min, tzinfo=UTC)
        if selected_date
        else None
    )
    selected_day_end = (
        selected_day_start + timedelta(days=1) if selected_day_start else None
    )
    range_last_date = range_end.date() - timedelta(days=1) if range_end else None

    context = {
        "symbol": SYMBOL,
        "daily_chart_data": [_kline_row(row) for row in daily_rows],
        "hourly_chart_data": [_kline_row(row) for row in hourly_rows],
        "oi_chart_data": [
            {
                "timestamp": _utc_iso(row.timestamp),
                "value": str(row.sum_open_interest),
            }
            for row in oi_rows
        ],
        "funding_chart_data": [
            {
                "timestamp": _utc_iso(row.funding_time),
                "value": str(row.funding_rate),
            }
            for row in funding_rows
        ],
        "selected_detail": _daily_detail(selected_daily),
        "selected_date": selected_date,
        "latest_date": latest_date,
        "earliest_date": daily_dates[0] if daily_dates else None,
        "previous_date": previous_date,
        "next_date": next_date,
        "range_start": range_start,
        "range_end": range_end,
        "range_last_date": range_last_date,
        "selected_day_start_iso": _utc_iso(selected_day_start) if selected_day_start else "",
        "selected_day_end_iso": _utc_iso(selected_day_end) if selected_day_end else "",
        "range_start_iso": _utc_iso(range_start) if range_start else "",
        "range_end_iso": _utc_iso(range_end) if range_end else "",
    }
    return render(request, "market_data/data_view.html", context)
