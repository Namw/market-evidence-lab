from __future__ import annotations

from datetime import datetime
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.market_data.derivatives import (
    BinanceFundingRateClient,
    BinanceOpenInterestClient,
    FundingRatePayload,
    OpenInterestPayload,
)
from apps.market_data.models import FundingRate, Kline, OpenInterest

from .models import CollectionRun
from .services import SUPPORTED_SYMBOL, _safe_error_message


EXCHANGE = Kline.Exchange.BINANCE
MARKET_TYPE = Kline.MarketType.USD_M_FUTURES
SUPPORTED_OI_PERIODS = {
    OpenInterest.Period.ONE_HOUR,
    OpenInterest.Period.FIVE_MINUTES,
}


@transaction.atomic
def _save_oi_batch(
    *, symbol: str, period: str, payloads: Iterable[OpenInterestPayload]
) -> tuple[int, int, int]:
    items = list(payloads)
    existing = {
        row.timestamp: row
        for row in OpenInterest.objects.filter(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=symbol,
            period=period,
            timestamp__in=[item.timestamp for item in items],
        )
    }
    creates, updates, skipped = [], [], 0
    now = timezone.now()
    for item in items:
        row = existing.get(item.timestamp)
        values = {
            "sum_open_interest": item.sum_open_interest,
            "sum_open_interest_value": item.sum_open_interest_value,
        }
        if row is None:
            creates.append(
                OpenInterest(
                    exchange=EXCHANGE,
                    market_type=MARKET_TYPE,
                    symbol=symbol,
                    period=period,
                    timestamp=item.timestamp,
                    **values,
                )
            )
        elif all(getattr(row, key) == value for key, value in values.items()):
            skipped += 1
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now
            updates.append(row)
    if creates:
        OpenInterest.objects.bulk_create(creates, batch_size=500)
    if updates:
        OpenInterest.objects.bulk_update(
            updates,
            ["sum_open_interest", "sum_open_interest_value", "updated_at"],
            batch_size=500,
        )
    return len(creates), len(updates), skipped


@transaction.atomic
def _save_funding_batch(
    *, symbol: str, payloads: Iterable[FundingRatePayload]
) -> tuple[int, int, int]:
    items = list(payloads)
    existing = {
        row.funding_time: row
        for row in FundingRate.objects.filter(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=symbol,
            funding_time__in=[item.funding_time for item in items],
        )
    }
    creates, updates, skipped = [], [], 0
    now = timezone.now()
    for item in items:
        row = existing.get(item.funding_time)
        values = {
            "funding_rate": item.funding_rate,
            "mark_price": item.mark_price,
            "rate_type": item.rate_type,
        }
        if row is None:
            creates.append(
                FundingRate(
                    exchange=EXCHANGE,
                    market_type=MARKET_TYPE,
                    symbol=symbol,
                    funding_time=item.funding_time,
                    **values,
                )
            )
        elif all(getattr(row, key) == value for key, value in values.items()):
            skipped += 1
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now
            updates.append(row)
    if creates:
        FundingRate.objects.bulk_create(creates, batch_size=500)
    if updates:
        FundingRate.objects.bulk_update(
            updates,
            ["funding_rate", "mark_price", "rate_type", "updated_at"],
            batch_size=500,
        )
    return len(creates), len(updates), skipped


def _run_collection(
    *,
    data_type: str,
    interval: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str,
    collector,
    iterator,
    save_batch,
    owns_client: bool,
    save_batch_kwargs: dict | None = None,
) -> CollectionRun:
    run = CollectionRun.objects.create(
        data_type=data_type,
        exchange=CollectionRun.Exchange.BINANCE,
        market_type=CollectionRun.MarketType.USD_M_FUTURES,
        symbol=symbol,
        interval=interval,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        status=CollectionRun.Status.RUNNING,
        started_at=timezone.now(),
    )
    inserted = updated = database_skipped = persisted = failed = 0
    try:
        for batch in iterator:
            created_count, updated_count, skipped_count = save_batch(
                symbol=symbol,
                payloads=batch,
                **(save_batch_kwargs or {}),
            )
            inserted += created_count
            updated += updated_count
            database_skipped += skipped_count
            persisted += created_count + updated_count
        run.status = CollectionRun.Status.SUCCESS
    except Exception as exc:
        failed = 1
        run.status = CollectionRun.Status.PARTIAL if persisted else CollectionRun.Status.FAILED
        run.error_message = _safe_error_message(exc)
    finally:
        run.finished_at = timezone.now()
        run.request_count = collector.request_count
        run.received_count = collector.received_count
        run.inserted_count = inserted
        run.updated_count = updated
        run.skipped_count = collector.skipped_count + database_skipped
        run.failed_count = failed
        run.save(
            update_fields=[
                "status", "finished_at", "request_count", "received_count",
                "inserted_count", "updated_count", "skipped_count", "failed_count",
                "error_message",
            ]
        )
        if owns_client:
            collector.close()
    return run


def collect_open_interest(
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = CollectionRun.Trigger.MANUAL,
    *,
    period: str = OpenInterest.Period.ONE_HOUR,
    client: BinanceOpenInterestClient | None = None,
) -> CollectionRun:
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if period not in SUPPORTED_OI_PERIODS:
        raise ValueError(f"Unsupported OI period: {period}")
    collector = client or BinanceOpenInterestClient()
    return _run_collection(
        data_type=CollectionRun.DataType.OPEN_INTEREST,
        interval=period,
        symbol=symbol,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        collector=collector,
        iterator=collector.iter_batches(
            symbol=symbol,
            period=period,
            range_start=range_start,
            range_end=range_end,
        ),
        save_batch=_save_oi_batch,
        save_batch_kwargs={"period": period},
        owns_client=client is None,
    )


def collect_funding_rates(
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = CollectionRun.Trigger.MANUAL,
    *,
    client: BinanceFundingRateClient | None = None,
) -> CollectionRun:
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"Unsupported symbol: {symbol}")
    collector = client or BinanceFundingRateClient()
    return _run_collection(
        data_type=CollectionRun.DataType.FUNDING,
        interval=CollectionRun.Interval.ACTUAL,
        symbol=symbol,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        collector=collector,
        iterator=collector.iter_batches(
            symbol=symbol,
            range_start=range_start,
            range_end=range_end,
        ),
        save_batch=_save_funding_batch,
        owns_client=client is None,
    )
