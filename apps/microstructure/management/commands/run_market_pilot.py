import json

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.microstructure.market_pilot import run_market_pilot


class Command(BaseCommand):
    help = "Run a read-only four-hour ETH market analysis pilot with DeepSeek."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            default=settings.MICROSTRUCTURE_SYMBOL,
            help="Market symbol (default: configured microstructure symbol).",
        )

    def handle(self, *args, **options):
        symbol = str(options["symbol"]).upper()
        try:
            report = run_market_pilot(symbol)
        except (RuntimeError, ValueError, httpx.HTTPError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
