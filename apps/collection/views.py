from django.contrib import messages
from django.db.models import Count, Max, Min
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.market_data.models import Kline

from .forms import CollectionForm
from .models import CollectionRun
from .services import SUPPORTED_SYMBOL, collect_klines


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
            runs = []
            for interval in form.cleaned_data["intervals"]:
                runs.append(
                    collect_klines(
                        SUPPORTED_SYMBOL,
                        interval,
                        form.range_start,
                        form.range_end,
                        trigger=CollectionRun.Trigger.MANUAL,
                    )
                )

            statuses = {run.status for run in runs}
            if statuses == {CollectionRun.Status.SUCCESS}:
                messages.success(request, "所选周期采集完成。")
            elif CollectionRun.Status.PARTIAL in statuses or (
                CollectionRun.Status.SUCCESS in statuses
                and CollectionRun.Status.FAILED in statuses
            ):
                messages.warning(request, "采集部分完成，请查看运行记录中的错误摘要。")
            else:
                messages.error(request, "采集失败，请查看运行记录中的错误摘要。")
            return redirect("collection:index")
    else:
        form = CollectionForm()

    context = {
        "form": form,
        "data_overview": _data_overview(),
        "recent_runs": CollectionRun.objects.all()[:20],
    }
    return render(request, "collection/index.html", context)
