from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from apps.inspection.models import (
    DerivativesInspectionRun,
    KlineInspectionRun,
    NewsInspectionRun,
)
from apps.inspection.news import inspect_news_collection
from apps.news_data.services import collect_news_feed, collect_news_source
from apps.market_data.models import OpenInterest
from apps.inspection.services import (
    inspect_funding_rates,
    inspect_klines,
    inspect_open_interest,
)

from .derivatives import collect_funding_rates, collect_open_interest
from .models import CollectionRun
from .services import collect_klines


@dataclass(frozen=True, slots=True)
class CollectionInspectionResult:
    collection_run: CollectionRun
    inspection_run: KlineInspectionRun | DerivativesInspectionRun | NewsInspectionRun


def collect_and_inspect(
    *,
    data_type: str,
    symbol: str = "",
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    trigger: str = CollectionRun.Trigger.MANUAL,
    interval: str | None = None,
    client=None,
    source_code: str | None = None,
    feed_code: str | None = None,
    safety_page_limit: int | None = None,
    between_steps_callback: Callable[[], None] | None = None,
) -> CollectionInspectionResult:
    """Run one raw collection and inspect the exact effective persisted range."""
    if data_type == CollectionRun.DataType.NEWS:
        if not source_code and not feed_code:
            raise ValueError("A news source or feed code is required.")
        kwargs = {}
        if safety_page_limit is not None:
            kwargs["safety_page_limit"] = safety_page_limit
        collector = collect_news_feed if feed_code else collect_news_source
        collection_run = collector(
            feed_code or source_code,
            trigger=trigger,
            range_end=range_end,
            client=client,
            **kwargs,
        )
        if between_steps_callback is not None:
            between_steps_callback()
        inspection_run = inspect_news_collection(collection_run)
        return CollectionInspectionResult(collection_run, inspection_run)
    if range_start is None or range_end is None:
        raise ValueError("A collection range is required.")
    if data_type == CollectionRun.DataType.KLINE:
        if interval not in {
            CollectionRun.Interval.ONE_DAY,
            CollectionRun.Interval.ONE_HOUR,
            CollectionRun.Interval.FIVE_MINUTES,
        }:
            raise ValueError("A supported Kline interval is required.")
        collection_run = collect_klines(
            symbol,
            interval,
            range_start,
            range_end,
            trigger=trigger,
            client=client,
        )
        if between_steps_callback is not None:
            between_steps_callback()
        inspection_run = inspect_klines(
            symbol,
            interval,
            collection_run.range_start,
            collection_run.range_end,
            trigger=trigger,
            source_collection_run=collection_run,
        )
    elif data_type == CollectionRun.DataType.OPEN_INTEREST:
        period = interval or OpenInterest.Period.ONE_HOUR
        period_steps = {
            OpenInterest.Period.ONE_HOUR: timedelta(hours=1),
            OpenInterest.Period.FIVE_MINUTES: timedelta(minutes=5),
        }
        if period not in period_steps:
            raise ValueError("A supported OI period is required.")
        # Binance OI timestamps represent period ends. Include the following
        # boundary so a requested set of UTC days can be checked end-to-end.
        effective_end = range_end + period_steps[period]
        collection_run = collect_open_interest(
            symbol,
            range_start,
            effective_end,
            trigger=trigger,
            period=period,
            client=client,
        )
        if between_steps_callback is not None:
            between_steps_callback()
        inspection_run = inspect_open_interest(
            symbol,
            collection_run.range_start,
            collection_run.range_end,
            trigger=trigger,
            period=period,
            source_collection_run=collection_run,
        )
    elif data_type == CollectionRun.DataType.FUNDING:
        collection_run = collect_funding_rates(
            symbol,
            range_start,
            range_end,
            trigger=trigger,
            client=client,
        )
        if between_steps_callback is not None:
            between_steps_callback()
        inspection_run = inspect_funding_rates(
            symbol,
            collection_run.range_start,
            collection_run.range_end,
            trigger=trigger,
            source_collection_run=collection_run,
        )
    else:
        raise ValueError(f"Unsupported collection data type: {data_type}")
    return CollectionInspectionResult(collection_run, inspection_run)
