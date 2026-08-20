from django.conf import settings
from django.core.management.base import BaseCommand

from apps.microstructure.research import refresh_future_5m_returns


class Command(BaseCommand):
    help = "Backfill strict, gap-aware future five-minute returns for market minutes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            default=settings.MICROSTRUCTURE_SYMBOL,
            help="Market symbol (default: configured microstructure symbol).",
        )

    def handle(self, *args, **options):
        symbol = str(options["symbol"]).upper()
        changed = refresh_future_5m_returns(symbol=symbol)
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {changed} future_5m_return rows for {symbol}."
            )
        )
