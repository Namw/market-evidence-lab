from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.news_analysis.objective_fact_validation import (
    ObjectiveValidationError,
    run_objective_fact_validation,
    write_report,
)


class Command(BaseCommand):
    help = "对指定数据库新闻逐篇调用 DeepSeek，执行临时只读客观事实提取验证"

    def add_arguments(self, parser):
        parser.add_argument("--news-id", action="append", type=int, required=True)
        parser.add_argument("--output", type=Path, required=True)

    def handle(self, *args, **options):
        try:
            report = run_objective_fact_validation(options["news_id"])
            output = write_report(report, options["output"])
        except (ObjectiveValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        stats = report["statistics"]
        self.stdout.write(
            self.style.SUCCESS(
                f"requested={stats['requested_news_count']} "
                f"success={stats['successful_extraction_count']} "
                f"validation_failed={stats['structure_or_content_validation_failure_count']} "
                f"json_failed={stats['json_parse_failure_count']} "
                f"api_failed={stats['ai_call_failure_count']} "
                f"output={output}"
            )
        )
