from django.core.management.base import BaseCommand, CommandError

from apps.news_analysis.models import ObjectiveFactExtractionRun
from apps.news_analysis.objective_facts import (
    ObjectiveFactAlreadyRunning,
    run_objective_fact_extraction,
)


class Command(BaseCommand):
    help = "逐篇运行正式新闻客观事实提取（只使用数据库已保存内容）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=[
                ObjectiveFactExtractionRun.Mode.INCREMENTAL,
                ObjectiveFactExtractionRun.Mode.RETRY_FAILED,
            ],
            default=ObjectiveFactExtractionRun.Mode.INCREMENTAL,
        )
        parser.add_argument("--news-id", action="append", type=int)
        parser.add_argument("--prompt-version")

    def handle(self, *args, **options):
        try:
            run = run_objective_fact_extraction(
                mode=options["mode"],
                trigger=ObjectiveFactExtractionRun.Trigger.COMMAND,
                triggered_by="management_command",
                record_ids=options.get("news_id"),
                prompt_version=options.get("prompt_version"),
            )
        except (ObjectiveFactAlreadyRunning, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"run={run.id} status={run.status} candidate={run.candidate_count} "
                f"requests={run.request_count} success={run.success_count} "
                f"failed={run.failed_count} skipped={run.skipped_count} "
                f"validation_passed={run.validation_passed_count} "
                f"validation_warning={run.validation_warning_count} "
                f"validation_error={run.validation_error_count} facts={run.facts_count}"
            )
        )
