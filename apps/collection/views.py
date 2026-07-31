from django.contrib import messages
from django.db.models import Count, Max, Min
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.market_data.models import FundingRate, Kline, OpenInterest

from .forms import CollectionForm, DerivativesCollectionForm
from .models import CollectionRun
from .pipeline import collect_and_inspect
from .services import SUPPORTED_SYMBOL


def _collection_results_message(request, results, success_message: str) -> None:
    collection_statuses = {result.collection_run.status for result in results}
    inspections = [result.inspection_run for result in results]
    if collection_statuses == {CollectionRun.Status.SUCCESS}:
        if all(
            run.status == "success" and run.quality_status == "passed"
            for run in inspections
        ):
            messages.success(request, success_message)
        elif any(run.status != "success" for run in inspections):
            messages.warning(request, "采集完成，但部分原始数据质量检查执行失败。")
        else:
            messages.warning(request, "采集完成，但原始数据质量检查发现问题。")
    elif CollectionRun.Status.PARTIAL in collection_statuses or (
        CollectionRun.Status.SUCCESS in collection_statuses
        and CollectionRun.Status.FAILED in collection_statuses
    ):
        messages.warning(request, "采集部分完成；原始数据质量检查结果已保存。")
    else:
        messages.error(request, "采集失败；原始数据质量检查结果已保存。")


def _data_overview():
    rows = {
        row["interval"]: row
        for row in Kline.objects.filter(
            exchange=Kline.Exchange.BINANCE,
            market_type=Kline.MarketType.USD_M_FUTURES,
            symbol=SUPPORTED_SYMBOL,
            interval__in=[Kline.Interval.ONE_DAY, Kline.Interval.ONE_HOUR],
        )
        .values("interval")
        .annotate(
            record_count=Count("id"),
            earliest_open_time=Min("open_time"),
            latest_open_time=Max("open_time"),
        )
    }
    return [
        {
            "interval": interval,
            "record_count": rows.get(interval, {}).get("record_count", 0),
            "earliest_open_time": rows.get(interval, {}).get("earliest_open_time"),
            "latest_open_time": rows.get(interval, {}).get("latest_open_time"),
        }
        for interval in (Kline.Interval.ONE_DAY, Kline.Interval.ONE_HOUR)
    ]


@require_http_methods(["GET", "POST"])
def collection_index(request):
    if request.method == "POST":
        form = CollectionForm(request.POST)
        if form.is_valid():
            results = []
            for interval in form.cleaned_data["intervals"]:
                results.append(
                    collect_and_inspect(
                        data_type=CollectionRun.DataType.KLINE,
                        symbol=SUPPORTED_SYMBOL,
                        interval=interval,
                        range_start=form.range_start,
                        range_end=form.range_end,
                        trigger=CollectionRun.Trigger.MANUAL,
                    )
                )
            _collection_results_message(
                request,
                results,
                "所选周期采集及原始数据质量检查完成并通过。",
            )
            return redirect("collection:index")
    else:
        form = CollectionForm()

    context = {
        "form": form,
        "data_overview": _data_overview(),
        "recent_runs": CollectionRun.objects.filter(
            data_type=CollectionRun.DataType.KLINE
        )[:20],
    }
    return render(request, "collection/index.html", context)


@require_http_methods(["GET", "POST"])
def derivatives_collection(request):
    if request.method == "POST":
        form = DerivativesCollectionForm(request.POST)
        if form.is_valid():
            results = []
            if CollectionRun.DataType.OPEN_INTEREST in form.cleaned_data["data_types"]:
                results.append(
                    collect_and_inspect(
                        data_type=CollectionRun.DataType.OPEN_INTEREST,
                        symbol=SUPPORTED_SYMBOL,
                        range_start=form.range_start,
                        range_end=form.range_end,
                        trigger=CollectionRun.Trigger.MANUAL,
                    )
                )
            if CollectionRun.DataType.FUNDING in form.cleaned_data["data_types"]:
                results.append(
                    collect_and_inspect(
                        data_type=CollectionRun.DataType.FUNDING,
                        symbol=SUPPORTED_SYMBOL,
                        range_start=form.range_start,
                        range_end=form.range_end,
                        trigger=CollectionRun.Trigger.MANUAL,
                    )
                )
            _collection_results_message(
                request,
                results,
                "所选衍生品数据采集及原始数据质量检查完成并通过。",
            )
            return redirect("collection:derivatives")
    else:
        form = DerivativesCollectionForm()

    overview = [
        {
            "label": "OI · 1h",
            "record_count": OpenInterest.objects.count(),
            "earliest": OpenInterest.objects.order_by("timestamp").values_list("timestamp", flat=True).first(),
            "latest": OpenInterest.objects.order_by("-timestamp").values_list("timestamp", flat=True).first(),
        },
        {
            "label": "Funding · 实际结算",
            "record_count": FundingRate.objects.count(),
            "earliest": FundingRate.objects.order_by("funding_time").values_list("funding_time", flat=True).first(),
            "latest": FundingRate.objects.order_by("-funding_time").values_list("funding_time", flat=True).first(),
        },
    ]
    return render(
        request,
        "collection/derivatives.html",
        {
            "form": form,
            "data_overview": overview,
            "recent_runs": CollectionRun.objects.filter(
                data_type__in=[
                    CollectionRun.DataType.OPEN_INTEREST,
                    CollectionRun.DataType.FUNDING,
                ]
            )[:20],
        },
    )
