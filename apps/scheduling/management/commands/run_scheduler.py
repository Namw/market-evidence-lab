import signal
import threading
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError

from apps.scheduling.services import (
    claim_due_schedules,
    execute_claimed_workflow,
    get_builtin_schedule,
    record_heartbeat,
)
from apps.scheduling.deribit_workflow import (
    claim_due_deribit_options_schedules,
    execute_claimed_deribit_options_workflow,
    get_builtin_deribit_options_schedule,
)
from apps.scheduling.news_workflow import (
    claim_due_news_schedules,
    execute_claimed_news_workflow,
    get_builtin_news_schedules,
)
from apps.scheduling.news_ai_workflow import (
    claim_due_news_ai_schedules,
    execute_claimed_news_ai_workflow,
    get_builtin_news_ai_schedule,
)
from apps.scheduling.funds_workflow import (
    claim_due_fund_schedules,
    execute_claimed_fund_workflow,
    get_builtin_fund_schedules,
)


class Command(BaseCommand):
    help = "Run the built-in market-data and news workflow schedule executor."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Check due schedules once, execute claimed runs, and exit.",
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=30,
            metavar="SECONDS",
            help="Seconds between checks (default: 30).",
        )

    def handle(self, *args, **options):
        poll_interval = options["poll_interval"]
        if not 1 <= poll_interval <= 3600:
            raise CommandError("--poll-interval must be between 1 and 3600 seconds")

        get_builtin_schedule()
        get_builtin_deribit_options_schedule()
        get_builtin_news_schedules()
        get_builtin_news_ai_schedule()
        get_builtin_fund_schedules()
        executor_id = str(uuid4())
        stop_event = threading.Event()

        def request_stop(signum, frame):
            stop_event.set()

        previous_handlers = {}
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, request_stop)

        def heartbeat(is_running=True):
            record_heartbeat(
                executor_id,
                poll_interval_seconds=poll_interval,
                is_running=is_running,
            )

        heartbeat()
        try:
            while not stop_event.is_set():
                heartbeat()
                try:
                    claimed_ids = claim_due_schedules()
                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Schedule claim failed ({exc.__class__.__name__}); retrying."
                        )
                    )
                    claimed_ids = []

                try:
                    claimed_news_ids = claim_due_news_schedules()
                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"News schedule claim failed ({exc.__class__.__name__}); retrying."
                        )
                    )
                    claimed_news_ids = []

                try:
                    claimed_news_ai_ids = claim_due_news_ai_schedules()
                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"News AI schedule claim failed ({exc.__class__.__name__}); retrying."
                        )
                    )
                    claimed_news_ai_ids = []

                try:
                    claimed_deribit_ids = claim_due_deribit_options_schedules()
                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Deribit schedule claim failed ({exc.__class__.__name__}); retrying."
                        )
                    )
                    claimed_deribit_ids = []

                try:
                    claimed_fund_ids = claim_due_fund_schedules()
                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Fund-data schedule claim failed ({exc.__class__.__name__}); retrying."
                        )
                    )
                    claimed_fund_ids = []

                for workflow_run_id in claimed_ids:
                    try:
                        execute_claimed_workflow(
                            workflow_run_id,
                            heartbeat_callback=heartbeat,
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f"WorkflowRun #{workflow_run_id} failed unexpectedly "
                                f"({exc.__class__.__name__}); continuing."
                            )
                        )
                    heartbeat()

                for workflow_run_id in claimed_news_ids:
                    try:
                        execute_claimed_news_workflow(
                            workflow_run_id,
                            heartbeat_callback=heartbeat,
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f"NewsWorkflowRun #{workflow_run_id} failed unexpectedly "
                                f"({exc.__class__.__name__}); continuing."
                            )
                        )
                    heartbeat()

                for workflow_run_id in claimed_news_ai_ids:
                    try:
                        execute_claimed_news_ai_workflow(
                            workflow_run_id,
                            heartbeat_callback=heartbeat,
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f"NewsAIWorkflowRun #{workflow_run_id} failed unexpectedly "
                                f"({exc.__class__.__name__}); continuing."
                            )
                        )
                    heartbeat()

                for workflow_run_id in claimed_deribit_ids:
                    try:
                        execute_claimed_deribit_options_workflow(
                            workflow_run_id,
                            heartbeat_callback=heartbeat,
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f"DeribitOptionsWorkflowRun #{workflow_run_id} "
                                f"failed unexpectedly ({exc.__class__.__name__}); continuing."
                            )
                        )
                    heartbeat()

                for workflow_run_id in claimed_fund_ids:
                    try:
                        execute_claimed_fund_workflow(
                            workflow_run_id,
                            heartbeat_callback=heartbeat,
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f"FundDataWorkflowRun #{workflow_run_id} failed unexpectedly "
                                f"({exc.__class__.__name__}); continuing."
                            )
                        )
                    heartbeat()

                if options["once"]:
                    break
                stop_event.wait(poll_interval)
        finally:
            heartbeat(is_running=False)
            for signal_number, previous_handler in previous_handlers.items():
                signal.signal(signal_number, previous_handler)

        self.stdout.write(self.style.SUCCESS("Scheduler check complete."))
