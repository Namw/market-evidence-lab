from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.microstructure.market_monitor import (
    MarketMonitorAlreadyRunning,
    monitor_market_windows,
)


class Command(BaseCommand):
    help = "Check recent closed ETH four-hour windows and analyze new anomalies."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="ETHUSDT")
        parser.add_argument("--threshold", default="2")
        parser.add_argument("--max-windows", type=int, default=6)

    def handle(self, *args, **options):
        try:
            threshold = Decimal(str(options["threshold"]))
        except InvalidOperation as exc:
            raise CommandError("--threshold must be numeric") from exc
        max_windows = options["max_windows"]
        if not 1 <= max_windows <= 24:
            raise CommandError("--max-windows must be between 1 and 24")
        try:
            run = monitor_market_windows(
                symbol=str(options["symbol"]).upper(),
                threshold_pct=threshold,
                max_windows=max_windows,
            )
        except MarketMonitorAlreadyRunning as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Market monitor run #{run.id}: {run.status}; "
                f"windows={run.window_count}; requests={run.request_count}; "
                f"tokens={run.total_tokens}."
            )
        )
