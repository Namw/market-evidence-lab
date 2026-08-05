from __future__ import annotations

import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.collection.models import CollectionRun

from .http import ResilientHttpClient
from .models import (
    EtfFlowDaily,
    SourceDiagnostic,
    StablecoinSupplyDaily,
)
from .parsers import parse_defillama_chart, parse_farside_html


DEFILLAMA_URL = "https://stablecoins.llama.fi/stablecoincharts/Ethereum"
FARSIDE_URL = "https://farside.co.uk/eth/"
ETHERSCAN_URL = "https://etherscan.io/accounts"
ETHERSCAN_POLICY_NOTE = (
    "Etherscan Terms prohibit robots, scrapers and automated data collection; "
    "live collection is disabled unless prior written permission is obtained."
)


def _safe_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: upstream collection failed"[:500]


def _new_run(data_type: str, *, trigger: str) -> CollectionRun:
    now = timezone.now()
    return CollectionRun.objects.create(
        data_type=data_type,
        symbol="Ethereum",
        interval=CollectionRun.Interval.ONE_DAY,
        range_start=now - timedelta(days=14),
        range_end=now,
        trigger=trigger,
        status=CollectionRun.Status.RUNNING,
        started_at=now,
    )


def _finish_run(
    run,
    *,
    client=None,
    received=0,
    created=0,
    updated=0,
    skipped=0,
    failed=0,
    error="",
):
    run.finished_at = timezone.now()
    run.request_count = getattr(client, "request_count", 0)
    run.received_count = received
    run.inserted_count = created
    run.updated_count = updated
    run.skipped_count = skipped
    run.failed_count = failed
    run.error_message = error
    run.status = (
        CollectionRun.Status.FAILED
        if failed and not created and not updated
        else CollectionRun.Status.PARTIAL
        if failed
        else CollectionRun.Status.SUCCESS
    )
    run.save()
    return run


def _update_diagnostic(source, url, response, *, metadata=None):
    SourceDiagnostic.objects.update_or_create(
        source=source,
        defaults={
            "source_url": url,
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "content_type": response.headers.get("content-type", "")[:120],
            "retrieved_at": timezone.now(),
            "metadata": metadata or {},
            "policy_status": "allowed",
            "policy_note": "",
        },
    )


@transaction.atomic
def _save_stablecoin_records(records, retrieved_at):
    dates = [item.observation_date for item in records]
    existing = {
        item.observation_date: item
        for item in StablecoinSupplyDaily.objects.filter(
            chain="Ethereum", stablecoin_symbol="", observation_date__in=dates
        )
    }
    to_create, to_update = [], []
    skipped = 0
    for item in records:
        values = {
            "circulating_supply": item.circulating_supply,
            "circulating_supply_usd": item.circulating_supply_usd,
            "minted_supply_usd": item.minted_supply_usd,
            "bridged_supply_usd": item.bridged_supply_usd,
            "source": "DeFiLlama",
            "source_url": DEFILLAMA_URL,
            "retrieved_at": retrieved_at,
        }
        current = existing.get(item.observation_date)
        if current is None:
            to_create.append(
                StablecoinSupplyDaily(
                    observation_date=item.observation_date,
                    chain="Ethereum",
                    stablecoin_symbol="",
                    **values,
                )
            )
        elif all(getattr(current, field) == value for field, value in values.items() if field != "retrieved_at"):
            skipped += 1
        else:
            for field, value in values.items():
                setattr(current, field, value)
            current.updated_at = retrieved_at
            to_update.append(current)
    StablecoinSupplyDaily.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        StablecoinSupplyDaily.objects.bulk_update(
            to_update,
            [
                "circulating_supply",
                "circulating_supply_usd",
                "minted_supply_usd",
                "bridged_supply_usd",
                "source",
                "source_url",
                "retrieved_at",
                "updated_at",
            ],
            batch_size=500,
        )
    return len(to_create), len(to_update), skipped


def collect_stablecoin_supply(
    *, trigger=CollectionRun.Trigger.MANUAL, client=None
) -> CollectionRun:
    run = _new_run(CollectionRun.DataType.STABLECOIN_SUPPLY, trigger=trigger)
    collector = client or ResilientHttpClient()
    owns_client = client is None
    try:
        response = collector.get(DEFILLAMA_URL)
        retrieved_at = timezone.now()
        records = parse_defillama_chart(response.text)
        has_history = StablecoinSupplyDaily.objects.filter(
            chain="Ethereum", stablecoin_symbol=""
        ).exists()
        selected = records[-7:] if has_history else records
        created, updated, skipped = _save_stablecoin_records(selected, retrieved_at)
        _update_diagnostic(
            "DeFiLlama",
            DEFILLAMA_URL,
            response,
            metadata={
                "rows_received": len(records),
                "rows_selected": len(selected),
                "first_date": records[0].observation_date.isoformat(),
                "last_date": records[-1].observation_date.isoformat(),
            },
        )
        return _finish_run(
            run,
            client=collector,
            received=len(records),
            created=created,
            updated=updated,
            skipped=skipped,
        )
    except Exception as exc:
        return _finish_run(run, client=collector, failed=1, error=_safe_error(exc))
    finally:
        if owns_client:
            collector.close()


@transaction.atomic
def _save_etf_records(records, retrieved_at):
    keys = [(item.trade_date, item.ticker) for item in records]
    dates = {item[0] for item in keys}
    existing = {
        (item.trade_date, item.ticker): item
        for item in EtfFlowDaily.objects.filter(trade_date__in=dates)
    }
    to_create, to_update = [], []
    skipped = 0
    for item in records:
        values = {
            "flow_usd": item.flow_usd,
            "raw_value": item.raw_value,
            "is_total": item.is_total,
            "source": "Farside",
            "source_url": FARSIDE_URL,
            "retrieved_at": retrieved_at,
        }
        current = existing.get((item.trade_date, item.ticker))
        if current is None:
            to_create.append(EtfFlowDaily(trade_date=item.trade_date, ticker=item.ticker, **values))
        elif all(getattr(current, field) == value for field, value in values.items() if field != "retrieved_at"):
            skipped += 1
        else:
            for field, value in values.items():
                setattr(current, field, value)
            current.updated_at = retrieved_at
            to_update.append(current)
    EtfFlowDaily.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        EtfFlowDaily.objects.bulk_update(
            to_update,
            ["flow_usd", "raw_value", "is_total", "source", "source_url", "retrieved_at", "updated_at"],
            batch_size=500,
        )
    return len(to_create), len(to_update), skipped


def collect_etf_flows(*, trigger=CollectionRun.Trigger.MANUAL, client=None) -> CollectionRun:
    run = _new_run(CollectionRun.DataType.ETH_ETF_FLOW, trigger=trigger)
    collector = client or ResilientHttpClient()
    owns_client = client is None
    try:
        response = collector.get(FARSIDE_URL)
        retrieved_at = timezone.now()
        records = parse_farside_html(response.text)
        latest = max(item.trade_date for item in records)
        cutoff = latest - timedelta(days=13)
        selected = [item for item in records if item.trade_date >= cutoff]
        created, updated, skipped = _save_etf_records(selected, retrieved_at)
        _update_diagnostic(
            "Farside",
            FARSIDE_URL,
            response,
            metadata={
                "rows_received": len(records),
                "rows_selected": len(selected),
                "latest_trade_date": latest.isoformat(),
                "tickers": sorted({item.ticker for item in records if not item.is_total}),
            },
        )
        return _finish_run(
            run,
            client=collector,
            received=len(records),
            created=created,
            updated=updated,
            skipped=skipped,
        )
    except Exception as exc:
        return _finish_run(run, client=collector, failed=1, error=_safe_error(exc))
    finally:
        if owns_client:
            collector.close()


def collect_address_balances(*, trigger=CollectionRun.Trigger.MANUAL) -> CollectionRun:
    run = _new_run(CollectionRun.DataType.ETH_ADDRESS_BALANCE, trigger=trigger)
    SourceDiagnostic.objects.update_or_create(
        source="Etherscan",
        defaults={
            "source_url": ETHERSCAN_URL,
            "policy_status": "blocked",
            "policy_note": ETHERSCAN_POLICY_NOTE,
            "metadata": {"live_collection_enabled": False},
        },
    )
    return _finish_run(
        run,
        failed=1,
        error="SourcePolicyBlocked: Etherscan automated collection is disabled by source terms",
    )
