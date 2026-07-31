from copy import deepcopy

from django.db import transaction

from .models import ResearchCase


CALCULATION_FIELDS = (
    "volume_average_20",
    "volume_ratio",
    "upper_wick_body_ratio",
    "upper_wick_range_ratio",
    "lower_wick_body_ratio",
    "lower_wick_range_ratio",
)


def case_identity_for_finding(finding):
    return {
        "exchange": finding.run.exchange,
        "market_type": finding.run.market_type,
        "symbol": finding.run.symbol,
        "interval": finding.run.interval,
        "event_time": finding.open_time,
    }


def _calculation_snapshot(finding):
    return {
        field: (
            None
            if getattr(finding, field) is None
            else format(getattr(finding, field).normalize(), "f")
        )
        for field in CALCULATION_FIELDS
    }


@transaction.atomic
def get_or_create_case_from_finding(finding):
    identity = case_identity_for_finding(finding)
    defaults = {
        "source_finding": finding,
        "title": f"{identity['symbol']} {identity['event_time']:%Y-%m-%d} 市场异常研究案例",
        "anomaly_signals_snapshot": deepcopy(finding.signals),
        "calculation_snapshot": _calculation_snapshot(finding),
        "open": finding.open,
        "high": finding.high,
        "low": finding.low,
        "close": finding.close,
        "volume": finding.volume,
        "price_change_pct": finding.price_change_pct,
        "amplitude_pct": finding.amplitude_pct,
    }
    return ResearchCase.objects.get_or_create(**identity, defaults=defaults)
