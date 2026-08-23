from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.microstructure.calculations import floor_time
from apps.microstructure.models import MarketMinute, MicrostructureCollectorRun
from apps.microstructure.shock_backtest import backtest_kline_shocks


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


class Command(BaseCommand):
    help = (
        "Backtest whether a large one-minute candle predicts another large "
        "absolute price move after an exact number of minutes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--run-id",
            type=int,
            default=1,
            help="Collector run to backtest (default: 1).",
        )
        parser.add_argument(
            "--thresholds",
            nargs="+",
            type=Decimal,
            default=[Decimal("0.3"), Decimal("0.8")],
            help="Percentage thresholds (default: 0.3 0.8).",
        )
        parser.add_argument(
            "--horizon",
            type=int,
            default=5,
            help="Future close horizon in minutes (default: 5).",
        )
        parser.add_argument(
            "--signal-metric",
            choices=["body", "range"],
            default="body",
            help="Large-candle definition: body or high-low range (default: body).",
        )

    def handle(self, *args, **options):
        try:
            run = MicrostructureCollectorRun.objects.get(pk=options["run_id"])
        except MicrostructureCollectorRun.DoesNotExist as exc:
            raise CommandError(f"Collector run #{options['run_id']} does not exist.") from exc

        started_at = floor_time(run.started_at or run.created_at, seconds=60)
        ended_at = floor_time(run.stopped_at or timezone.now(), seconds=60) + timedelta(
            minutes=1
        )
        rows = list(
            MarketMinute.objects.filter(
                symbol=run.symbol,
                minute_start__gte=started_at,
                minute_start__lt=ended_at,
            ).order_by("minute_start")
        )
        if not rows:
            raise CommandError(f"Collector run #{run.pk} has no minute data.")

        try:
            results = backtest_kline_shocks(
                rows,
                thresholds_pct=options["thresholds"],
                horizon_minutes=options["horizon"],
                signal_metric=options["signal_metric"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        metric_label = (
            "|close/open - 1|"
            if options["signal_metric"] == "body"
            else "(high - low) / open"
        )
        self.stdout.write(
            f"Run #{run.pk} | {run.symbol} | {started_at.isoformat()} -> "
            f"{ended_at.isoformat()}"
        )
        self.stdout.write(
            f"Signal: {metric_label}; outcome: "
            f"|close[t+{options['horizon']}m]/close[t] - 1|"
        )
        self.stdout.write("")

        for result in results:
            interval = result.confidence_interval_95
            interval_text = (
                "n/a"
                if interval is None
                else f"{interval[0] * 100:.2f}%–{interval[1] * 100:.2f}%"
            )
            lift = "n/a" if result.lift is None else f"{result.lift:.2f}x"
            self.stdout.write(f"Threshold >= {result.threshold_pct}%")
            self.stdout.write(
                f"  conditional: {result.hit_count}/{result.signal_count} = "
                f"{_percent(result.probability)} (95% CI {interval_text})"
            )
            self.stdout.write(
                f"  baseline:    {result.baseline_hit_count}/"
                f"{result.valid_window_count} = "
                f"{_percent(result.baseline_probability)}; lift {lift}"
            )
            self.stdout.write(
                f"  signals:     up {result.positive_signal_count}, "
                f"down {result.negative_signal_count}, "
                f"flat {result.neutral_signal_count}"
            )
            self.stdout.write(
                f"  future hits: continuation {result.continuation_count}, "
                f"reversal {result.reversal_count}, "
                f"no direction {result.neutral_hit_count}"
            )
