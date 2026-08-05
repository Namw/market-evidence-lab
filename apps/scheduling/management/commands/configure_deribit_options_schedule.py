from datetime import time

from django.core.management.base import BaseCommand, CommandError

from apps.scheduling.deribit_workflow import (
    get_builtin_deribit_options_schedule,
)
from apps.scheduling.models import SCHEDULE_TIMEZONE
from apps.scheduling.services import calculate_next_run_at


class Command(BaseCommand):
    help = "Enable, disable, or configure the built-in Deribit ETH options schedule."

    def add_arguments(self, parser):
        state = parser.add_mutually_exclusive_group()
        state.add_argument("--enable", action="store_true")
        state.add_argument("--disable", action="store_true")
        parser.add_argument("--run-time", default=None, metavar="HH:MM")
        parser.add_argument("--dvol-lookback-days", type=int, default=None)

    def handle(self, *args, **options):
        schedule = get_builtin_deribit_options_schedule()
        lookback = options["dvol_lookback_days"] or schedule.dvol_lookback_days
        run_time = schedule.run_time
        if options["run_time"]:
            try:
                run_time = time.fromisoformat(options["run_time"])
            except ValueError as exc:
                raise CommandError("--run-time must use HH:MM") from exc
        if not 1 <= lookback <= 30:
            raise CommandError("--dvol-lookback-days must be between 1 and 30")
        if options["enable"]:
            schedule.enabled = True
        elif options["disable"]:
            schedule.enabled = False
        schedule.run_time = run_time
        schedule.timezone = SCHEDULE_TIMEZONE
        schedule.dvol_lookback_days = lookback
        schedule.next_run_at = calculate_next_run_at(run_time)
        schedule.save(
            update_fields=[
                "enabled",
                "run_time",
                "timezone",
                "dvol_lookback_days",
                "next_run_at",
                "updated_at",
            ]
        )
        state = "enabled" if schedule.enabled else "disabled"
        self.stdout.write(
            self.style.SUCCESS(
                f"Deribit options schedule {state}; daily={run_time:%H:%M} "
                f"{SCHEDULE_TIMEZONE}; "
                f"DVOL lookback={lookback}d; next={schedule.next_run_at.isoformat()}"
            )
        )
