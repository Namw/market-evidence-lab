from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from apps.inspection.models import DerivativesInspectionRun, KlineInspectionRun
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
    inspection_run: KlineInspectionRun | DerivativesInspectionRun


def collect_and_inspect(
    *,
    data_type: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = CollectionRun.Trigger.MANUAL,
    interval: str | None = None,
    client=None,
    between_steps_callback: Callable[[], None] | None = None,
) -> CollectionInspectionResult:
    """Run one raw collection and inspect the exact effective persisted range."""
    if data_type == CollectionRun.DataType.KLINE:
        if interval not in {
            CollectionRun.Interval.ONE_DAY,
            CollectionRun.Interval.ONE_HOUR,
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
        # Binance OI 1h timestamps represent period ends. Include the end-date
        # boundary so a requested set of UTC days can be checked end-to-end.
        effective_end = range_end + timedelta(hours=1)
        collection_run = collect_open_interest(
            symbol,
            range_start,
            effective_end,
            trigger=trigger,
            client=client,
        )
        if between_steps_callback is not None:
            between_steps_callback()
        inspection_run = inspect_open_interest(
            symbol,
            collection_run.range_start,
            collection_run.range_end,
            trigger=trigger,
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
