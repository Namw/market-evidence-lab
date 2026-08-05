from __future__ import annotations

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from apps.collection.models import CollectionRun

from .models import (
    AddressBalanceDaily,
    EtfFlowDaily,
    FundDataInspectionRun,
    StablecoinSupplyDaily,
    empty_quality_details,
)


def inspect_fund_data(task_type: str, collection_run: CollectionRun):
    run = FundDataInspectionRun.objects.create(
        task_type=task_type,
        source_collection_run=collection_run,
        started_at=timezone.now(),
    )
    details = empty_quality_details()
    try:
        if task_type == FundDataInspectionRun.TaskType.STABLECOIN:
            rows = list(
                StablecoinSupplyDaily.objects.filter(
                    chain="Ethereum", stablecoin_symbol=""
                ).order_by("observation_date")
            )
            if rows:
                expected = {
                    rows[0].observation_date + timedelta(days=offset)
                    for offset in range(
                        (rows[-1].observation_date - rows[0].observation_date).days
                        + 1
                    )
                }
                actual = {item.observation_date for item in rows}
                details["missing_dates"] = [
                    item.isoformat() for item in sorted(expected - actual)
                ][:200]
            invalid = [
                item.observation_date.isoformat()
                for item in rows
                if item.circulating_supply < 0 or item.circulating_supply_usd < 0
            ]
        elif task_type == FundDataInspectionRun.TaskType.ETF:
            rows = list(EtfFlowDaily.objects.order_by("trade_date", "ticker"))
            invalid = [f"{item.trade_date}:{item.ticker}" for item in rows if not item.ticker]
            total_counts = (
                EtfFlowDaily.objects.filter(is_total=True)
                .values("trade_date")
                .annotate(count=Count("id"))
                .filter(count__gt=1)
            )
            details["duplicate_keys"] = [
                item["trade_date"].isoformat() for item in total_counts
            ]
        elif task_type == FundDataInspectionRun.TaskType.ADDRESSES:
            if collection_run.status == CollectionRun.Status.FAILED and "SourcePolicyBlocked" in collection_run.error_message:
                run.status = FundDataInspectionRun.Status.SUCCESS
                run.quality_status = FundDataInspectionRun.QualityStatus.BLOCKED
                run.safe_error_summary = collection_run.error_message[:500]
                details["no_new_data"] = True
                rows = []
                invalid = []
            else:
                latest = (
                    AddressBalanceDaily.objects.order_by("-snapshot_date")
                    .values_list("snapshot_date", flat=True)
                    .first()
                )
                rows = list(
                    AddressBalanceDaily.objects.filter(snapshot_date=latest)
                    if latest
                    else []
                )
                invalid = [
                    f"{item.snapshot_date}:{item.rank}"
                    for item in rows
                    if item.balance_eth < 0 or not 1 <= item.rank <= 1000
                ]
        else:
            raise ValueError("unknown fund data task type")

        if run.quality_status != FundDataInspectionRun.QualityStatus.BLOCKED:
            details["no_new_data"] = collection_run.received_count == 0
            details["invalid_rows"] = invalid[:200]
            run.actual_count = len(rows)
            run.duplicate_count = len(details["duplicate_keys"])
            run.missing_count = len(details["missing_dates"])
            run.invalid_count = len(invalid)
            run.issue_count = run.duplicate_count + run.missing_count + run.invalid_count
            run.status = FundDataInspectionRun.Status.SUCCESS
            run.quality_status = (
                FundDataInspectionRun.QualityStatus.ISSUES
                if run.issue_count
                else FundDataInspectionRun.QualityStatus.PASSED
            )
        run.details = details
    except Exception as exc:
        run.status = FundDataInspectionRun.Status.FAILED
        run.quality_status = FundDataInspectionRun.QualityStatus.PENDING
        run.safe_error_summary = f"{exc.__class__.__name__}: quality inspection failed"
    run.finished_at = timezone.now()
    run.save()
    return run
