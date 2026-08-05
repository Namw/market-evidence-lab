from datetime import UTC, date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.collection.deribit import (
    collect_deribit_dvol,
    collect_deribit_option_instruments,
    collect_deribit_option_snapshot,
)
from apps.collection.models import CollectionRun


class Command(BaseCommand):
    help = "Collect ETH DVOL, active Deribit option instruments, and one 5m option snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dvol-start",
            type=date.fromisoformat,
            metavar="YYYY-MM-DD",
            help="Backfill hourly ETH DVOL from this UTC date (default: last 3 days).",
        )
        parser.add_argument(
            "--skip-dvol",
            action="store_true",
            help="Skip ETH DVOL collection.",
        )
        parser.add_argument(
            "--skip-snapshot",
            action="store_true",
            help="Only synchronize instruments and optionally DVOL.",
        )

    def handle(self, *args, **options):
        now = timezone.now().astimezone(UTC)
        observed_at = now.replace(
            minute=now.minute - now.minute % 5,
            second=0,
            microsecond=0,
        )
        runs: list[CollectionRun] = []

        if not options["skip_dvol"]:
            dvol_end = now.replace(minute=0, second=0, microsecond=0)
            start_date = options.get("dvol_start")
            dvol_start = (
                datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
                if start_date
                else dvol_end - timedelta(days=3)
            )
            if dvol_start >= dvol_end:
                raise CommandError("--dvol-start must be earlier than the current UTC hour")
            runs.append(collect_deribit_dvol(dvol_start, dvol_end))

        instrument_run = collect_deribit_option_instruments(observed_at=observed_at)
        runs.append(instrument_run)
        if (
            not options["skip_snapshot"]
            and instrument_run.status == CollectionRun.Status.SUCCESS
        ):
            runs.append(collect_deribit_option_snapshot(observed_at=observed_at))

        for run in runs:
            self.stdout.write(
                f"{run.data_type}: {run.status}; received={run.received_count}; "
                f"inserted={run.inserted_count}; updated={run.updated_count}; "
                f"skipped={run.skipped_count}"
            )
        failed = [run for run in runs if run.status != CollectionRun.Status.SUCCESS]
        if failed:
            names = ", ".join(run.data_type for run in failed)
            raise CommandError(f"Deribit collection did not complete successfully: {names}")
        self.stdout.write(self.style.SUCCESS("Deribit option collection complete."))
