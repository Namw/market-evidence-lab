from django.core.management.base import BaseCommand, CommandError

from apps.scheduling.funds_workflow import (
    FundWorkflowAlreadyRunning,
    execute_manual_fund_workflow,
)
from apps.scheduling.models import FundDataSchedule


class Command(BaseCommand):
    help = "Collect and inspect one free ETH funds observation dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            "task",
            choices=[item.value for item in FundDataSchedule.TaskType],
            help="stablecoin, etf, or addresses",
        )

    def handle(self, *args, **options):
        try:
            run = execute_manual_fund_workflow(options["task"])
        except FundWorkflowAlreadyRunning as exc:
            raise CommandError("A workflow for this fund-data task is already running.") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"FundDataWorkflowRun #{run.pk}: {run.status}; quality={run.quality_status}; "
                f"received={run.received_count}; inserted={run.inserted_count}; "
                f"updated={run.updated_count}; skipped={run.skipped_count}; failed={run.failed_count}"
            )
        )
