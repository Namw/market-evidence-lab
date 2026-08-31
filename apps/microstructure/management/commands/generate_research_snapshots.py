from django.conf import settings
from django.core.management.base import BaseCommand

from apps.scheduling.research_snapshot_workflow import generate_research_snapshot


class Command(BaseCommand):
    help = "Refresh five-minute labels and persist page-ready research snapshots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            action="append",
            dest="symbols",
            help="Symbol to generate; repeat for multiple symbols (default: all configured).",
        )

    def handle(self, *args, **options):
        symbols = options["symbols"] or settings.MICROSTRUCTURE_SYMBOLS
        for symbol in symbols:
            snapshot = generate_research_snapshot(str(symbol).upper())
            self.stdout.write(
                self.style.SUCCESS(
                    f"{snapshot.symbol} snapshot #{snapshot.pk}: "
                    f"minutes={snapshot.minute_count}, labels={snapshot.labeled_count}, "
                    f"updated={snapshot.labels_updated}, duration={snapshot.duration_ms}ms."
                )
            )
