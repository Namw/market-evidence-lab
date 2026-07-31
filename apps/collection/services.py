from __future__ import annotations

from datetime import datetime
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.market_data.binance import BinanceKlineClient, KlinePayload
from apps.market_data.models import Kline

from .models import CollectionRun


EXCHANGE = Kline.Exchange.BINANCE
MARKET_TYPE = Kline.MarketType.USD_M_FUTURES
SUPPORTED_SYMBOL = "ETHUSDT"
SUPPORTED_INTERVALS = {Kline.Interval.ONE_DAY, Kline.Interval.ONE_HOUR}
KLINE_UPDATE_FIELDS = [
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:1_000]


@transaction.atomic
def _save_batch(
    *,
    symbol: str,
    interval: str,
    payloads: Iterable[KlinePayload],
) -> tuple[int, int, int]:
    payload_list = list(payloads)
    if not payload_list:
        return 0, 0, 0

    open_times = [payload.open_time for payload in payload_list]
    existing_by_open_time = {
        item.open_time: item
        for item in Kline.objects.filter(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=symbol,
            interval=interval,
            open_time__in=open_times,
        )
    }

    to_create: list[Kline] = []
    to_update: list[Kline] = []
    skipped = 0
    updated_at = timezone.now()

    for payload in payload_list:
        existing = existing_by_open_time.get(payload.open_time)
        values = {field: getattr(payload, field) for field in KLINE_UPDATE_FIELDS}
        if existing is None:
            to_create.append(
                Kline(
                    exchange=EXCHANGE,
                    market_type=MARKET_TYPE,
                    symbol=symbol,
                    interval=interval,
                    open_time=payload.open_time,
                    **values,
                )
            )
            continue

        if all(getattr(existing, field) == value for field, value in values.items()):
            skipped += 1
            continue
        for field, value in values.items():
            setattr(existing, field, value)
        existing.updated_at = updated_at
        to_update.append(existing)

    if to_create:
        Kline.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        Kline.objects.bulk_update(
            to_update,
            [*KLINE_UPDATE_FIELDS, "updated_at"],
            batch_size=500,
        )
    return len(to_create), len(to_update), skipped


def collect_klines(
    symbol: str,
    interval: str,
    range_start: datetime,
    range_end: datetime,
    trigger: str = CollectionRun.Trigger.MANUAL,
    *,
    client: BinanceKlineClient | None = None,
) -> CollectionRun:
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")
    if range_start >= range_end:
        raise ValueError("range_start must be earlier than range_end")

    run = CollectionRun.objects.create(
        data_type=CollectionRun.DataType.KLINE,
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

    collector = client or BinanceKlineClient()
    owns_client = client is None
    inserted_count = 0
    updated_count = 0
    database_skipped_count = 0
    persisted_count = 0

    try:
        for payload_batch in collector.iter_batches(
            symbol=symbol,
            interval=interval,
            range_start=range_start,
            range_end=range_end,
        ):
            inserted, updated, skipped = _save_batch(
                symbol=symbol,
                interval=interval,
                payloads=payload_batch,
            )
            inserted_count += inserted
            updated_count += updated
            database_skipped_count += skipped
            persisted_count += inserted + updated
        run.status = CollectionRun.Status.SUCCESS
    except Exception as exc:  # The run record is the service boundary for collection errors.
        run.status = (
            CollectionRun.Status.PARTIAL
            if persisted_count > 0
            else CollectionRun.Status.FAILED
        )
        run.error_message = _safe_error_message(exc)
    finally:
        run.finished_at = timezone.now()
        run.request_count = collector.request_count
        run.received_count = collector.received_count
        run.inserted_count = inserted_count
        run.updated_count = updated_count
        run.skipped_count = collector.skipped_count + database_skipped_count
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "request_count",
                "received_count",
                "inserted_count",
                "updated_count",
                "skipped_count",
                "error_message",
            ]
        )
        if owns_client:
            collector.close()

    return run
