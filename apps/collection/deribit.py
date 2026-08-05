from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.market_data.deribit import (
    DvolCandlePayload,
    DeribitPublicClient,
    OptionInstrumentPayload,
    OptionSummaryPayload,
    SUPPORTED_CURRENCY,
)
from apps.market_data.models import (
    DeribitOptionInstrument,
    DeribitOptionMarketSnapshot,
    DeribitVolatilityIndexCandle,
)

from .models import CollectionRun
from .services import _safe_error_message


DVOL_UPDATE_FIELDS = ["close_time", "open", "high", "low", "close"]
INSTRUMENT_UPDATE_FIELDS = [
    "instrument_id",
    "base_currency",
    "quote_currency",
    "settlement_currency",
    "option_type",
    "strike",
    "expiration_time",
    "creation_time",
    "contract_size",
    "is_active",
    "state",
]
SNAPSHOT_UPDATE_FIELDS = [
    "source_timestamp",
    "underlying_price",
    "mark_price",
    "mark_iv",
    "bid_price",
    "ask_price",
    "mid_price",
    "last_price",
    "open_interest",
    "volume_24h",
    "volume_usd_24h",
    "interest_rate",
]


def floor_to_five_minutes(value: datetime) -> datetime:
    if timezone.is_naive(value):
        raise ValueError("observed_at must be timezone-aware")
    utc_value = value.astimezone(UTC)
    return utc_value.replace(
        minute=utc_value.minute - utc_value.minute % 5,
        second=0,
        microsecond=0,
    )


@transaction.atomic
def _save_dvol_batch(
    payloads: Iterable[DvolCandlePayload],
) -> tuple[int, int, int]:
    items = list(payloads)
    existing = {
        row.open_time: row
        for row in DeribitVolatilityIndexCandle.objects.filter(
            currency=DeribitVolatilityIndexCandle.Currency.ETH,
            resolution=DeribitVolatilityIndexCandle.Resolution.ONE_HOUR,
            open_time__in=[item.open_time for item in items],
        )
    }
    creates, updates, skipped = [], [], 0
    updated_at = timezone.now()
    for item in items:
        row = existing.get(item.open_time)
        values = {field: getattr(item, field) for field in DVOL_UPDATE_FIELDS}
        if row is None:
            creates.append(
                DeribitVolatilityIndexCandle(
                    currency=DeribitVolatilityIndexCandle.Currency.ETH,
                    resolution=DeribitVolatilityIndexCandle.Resolution.ONE_HOUR,
                    open_time=item.open_time,
                    **values,
                )
            )
        elif all(getattr(row, field) == value for field, value in values.items()):
            skipped += 1
        else:
            for field, value in values.items():
                setattr(row, field, value)
            row.updated_at = updated_at
            updates.append(row)
    if creates:
        DeribitVolatilityIndexCandle.objects.bulk_create(creates, batch_size=500)
    if updates:
        DeribitVolatilityIndexCandle.objects.bulk_update(
            updates, [*DVOL_UPDATE_FIELDS, "updated_at"], batch_size=500
        )
    return len(creates), len(updates), skipped


@transaction.atomic
def _save_option_instruments(
    payloads: Iterable[OptionInstrumentPayload],
) -> tuple[int, int, int]:
    items = list(payloads)
    if not items:
        raise ValueError("Deribit returned no active ETH option instruments.")
    existing = {
        row.instrument_name: row
        for row in DeribitOptionInstrument.objects.filter(
            instrument_name__in=[item.instrument_name for item in items]
        )
    }
    creates, updates, skipped = [], [], 0
    updated_at = timezone.now()
    active_names = {item.instrument_name for item in items}
    for item in items:
        row = existing.get(item.instrument_name)
        values = {field: getattr(item, field) for field in INSTRUMENT_UPDATE_FIELDS}
        if row is None:
            creates.append(
                DeribitOptionInstrument(
                    instrument_name=item.instrument_name,
                    **values,
                )
            )
        elif all(getattr(row, field) == value for field, value in values.items()):
            skipped += 1
        else:
            for field, value in values.items():
                setattr(row, field, value)
            row.updated_at = updated_at
            updates.append(row)
    deactivated = list(
        DeribitOptionInstrument.objects.filter(
            base_currency=SUPPORTED_CURRENCY,
            is_active=True,
        ).exclude(instrument_name__in=active_names)
    )
    for row in deactivated:
        row.is_active = False
        row.state = "expired"
        row.updated_at = updated_at
    if creates:
        DeribitOptionInstrument.objects.bulk_create(creates, batch_size=500)
    if updates:
        DeribitOptionInstrument.objects.bulk_update(
            updates, [*INSTRUMENT_UPDATE_FIELDS, "updated_at"], batch_size=500
        )
    if deactivated:
        DeribitOptionInstrument.objects.bulk_update(
            deactivated, ["is_active", "state", "updated_at"], batch_size=500
        )
    return len(creates), len(updates) + len(deactivated), skipped


@transaction.atomic
def _save_option_snapshots(
    *,
    observed_at: datetime,
    payloads: Iterable[OptionSummaryPayload],
) -> tuple[int, int, int]:
    items = list(payloads)
    names = [item.instrument_name for item in items]
    instruments = {
        row.instrument_name: row
        for row in DeribitOptionInstrument.objects.filter(instrument_name__in=names)
    }
    missing_names = sorted(set(names) - set(instruments))
    if missing_names:
        raise ValueError(
            f"{len(missing_names)} Deribit option instrument(s) are not synchronized."
        )
    existing = {
        row.instrument_id: row
        for row in DeribitOptionMarketSnapshot.objects.filter(
            observed_at=observed_at,
            instrument__instrument_name__in=names,
        )
    }
    creates, updates, skipped = [], [], 0
    updated_at = timezone.now()
    for item in items:
        instrument = instruments[item.instrument_name]
        row = existing.get(instrument.id)
        values = {field: getattr(item, field) for field in SNAPSHOT_UPDATE_FIELDS}
        if row is None:
            creates.append(
                DeribitOptionMarketSnapshot(
                    instrument=instrument,
                    observed_at=observed_at,
                    **values,
                )
            )
        elif all(getattr(row, field) == value for field, value in values.items()):
            skipped += 1
        else:
            for field, value in values.items():
                setattr(row, field, value)
            row.updated_at = updated_at
            updates.append(row)
    if creates:
        DeribitOptionMarketSnapshot.objects.bulk_create(creates, batch_size=500)
    if updates:
        DeribitOptionMarketSnapshot.objects.bulk_update(
            updates, [*SNAPSHOT_UPDATE_FIELDS, "updated_at"], batch_size=500
        )
    return len(creates), len(updates), skipped


def _create_run(
    *, data_type: str, interval: str, range_start: datetime, range_end: datetime, trigger: str
) -> CollectionRun:
    return CollectionRun.objects.create(
        data_type=data_type,
        exchange=CollectionRun.Exchange.DERIBIT,
        market_type=CollectionRun.MarketType.OPTIONS,
        symbol=SUPPORTED_CURRENCY,
        interval=interval,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
        status=CollectionRun.Status.RUNNING,
        started_at=timezone.now(),
    )


def _finish_run(
    run: CollectionRun,
    *,
    client: DeribitPublicClient,
    inserted: int,
    updated: int,
    skipped: int,
    failed: int,
) -> CollectionRun:
    run.finished_at = timezone.now()
    run.request_count = client.request_count
    run.received_count = client.received_count
    run.inserted_count = inserted
    run.updated_count = updated
    run.skipped_count = client.skipped_count + skipped
    run.failed_count = failed
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "request_count",
            "received_count",
            "inserted_count",
            "updated_count",
            "skipped_count",
            "failed_count",
            "error_message",
        ]
    )
    return run


def collect_deribit_dvol(
    range_start: datetime,
    range_end: datetime,
    trigger: str = CollectionRun.Trigger.MANUAL,
    *,
    client: DeribitPublicClient | None = None,
) -> CollectionRun:
    if range_start >= range_end:
        raise ValueError("range_start must be earlier than range_end")
    run = _create_run(
        data_type=CollectionRun.DataType.DERIBIT_DVOL,
        interval=CollectionRun.Interval.ONE_HOUR,
        range_start=range_start,
        range_end=range_end,
        trigger=trigger,
    )
    collector = client or DeribitPublicClient()
    inserted = updated = skipped = persisted = failed = 0
    try:
        for batch in collector.iter_dvol_batches(
            currency=SUPPORTED_CURRENCY,
            resolution="1h",
            range_start=range_start,
            range_end=range_end,
        ):
            created_count, updated_count, skipped_count = _save_dvol_batch(batch)
            inserted += created_count
            updated += updated_count
            skipped += skipped_count
            persisted += created_count + updated_count
        run.status = CollectionRun.Status.SUCCESS
    except Exception as exc:
        failed = 1
        run.status = CollectionRun.Status.PARTIAL if persisted else CollectionRun.Status.FAILED
        run.error_message = _safe_error_message(exc)
    finally:
        _finish_run(
            run,
            client=collector,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            failed=failed,
        )
        if client is None:
            collector.close()
    return run


def collect_deribit_option_instruments(
    trigger: str = CollectionRun.Trigger.MANUAL,
    *,
    observed_at: datetime | None = None,
    client: DeribitPublicClient | None = None,
) -> CollectionRun:
    observed = floor_to_five_minutes(observed_at or timezone.now())
    run = _create_run(
        data_type=CollectionRun.DataType.DERIBIT_OPTION_INSTRUMENT,
        interval=CollectionRun.Interval.ACTUAL,
        range_start=observed,
        range_end=observed + timedelta(minutes=5),
        trigger=trigger,
    )
    collector = client or DeribitPublicClient()
    inserted = updated = skipped = failed = 0
    try:
        inserted, updated, skipped = _save_option_instruments(
            collector.fetch_option_instruments(currency=SUPPORTED_CURRENCY)
        )
        run.status = CollectionRun.Status.SUCCESS
    except Exception as exc:
        failed = 1
        run.status = CollectionRun.Status.FAILED
        run.error_message = _safe_error_message(exc)
    finally:
        _finish_run(
            run,
            client=collector,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            failed=failed,
        )
        if client is None:
            collector.close()
    return run


def collect_deribit_option_snapshot(
    trigger: str = CollectionRun.Trigger.MANUAL,
    *,
    observed_at: datetime | None = None,
    client: DeribitPublicClient | None = None,
) -> CollectionRun:
    observed = floor_to_five_minutes(observed_at or timezone.now())
    run = _create_run(
        data_type=CollectionRun.DataType.DERIBIT_OPTION_SNAPSHOT,
        interval=CollectionRun.Interval.FIVE_MINUTES,
        range_start=observed,
        range_end=observed + timedelta(minutes=5),
        trigger=trigger,
    )
    collector = client or DeribitPublicClient()
    inserted = updated = skipped = failed = 0
    try:
        inserted, updated, skipped = _save_option_snapshots(
            observed_at=observed,
            payloads=collector.fetch_option_summaries(currency=SUPPORTED_CURRENCY),
        )
        run.status = CollectionRun.Status.SUCCESS
    except Exception as exc:
        failed = 1
        run.status = CollectionRun.Status.FAILED
        run.error_message = _safe_error_message(exc)
    finally:
        _finish_run(
            run,
            client=collector,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            failed=failed,
        )
        if client is None:
            collector.close()
    return run
