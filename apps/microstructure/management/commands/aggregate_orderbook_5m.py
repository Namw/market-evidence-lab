from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.microstructure.calculations import floor_time
from apps.microstructure.services import aggregate_range


def parse_aware_datetime(value: str) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise CommandError(
            "Use an ISO-8601 datetime with an explicit timezone, "
            "for example 2026-08-17T00:00:00Z."
        )
    return parsed.astimezone(UTC)


class Command(BaseCommand):
    help = "Aggregate one-second order-book snapshots into UTC five-minute summaries."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default=settings.MICROSTRUCTURE_SYMBOL)
        parser.add_argument("--start", help="Inclusive ISO-8601 start time.")
        parser.add_argument("--end", help="Exclusive ISO-8601 end time.")

    def handle(self, *args, **options):
        start_value = options.get("start")
        end_value = options.get("end")
        if bool(start_value) != bool(end_value):
            raise CommandError("--start and --end must be provided together.")
        if start_value:
            range_start = parse_aware_datetime(start_value)
            range_end = parse_aware_datetime(end_value)
        else:
            range_end = floor_time(timezone.now(), seconds=300)
            range_start = range_end - timedelta(minutes=5)
        if range_start >= range_end:
            raise CommandError("--start must be earlier than --end.")

        try:
            written, empty = aggregate_range(
                symbol=options["symbol"].upper(),
                range_start=range_start,
                range_end=range_end,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"5m aggregation complete; written={written}; empty={empty}."
            )
        )
