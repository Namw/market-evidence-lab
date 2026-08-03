from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.news_analysis.fact_validation import (
    FactExtractionError,
    run_fact_validation,
    write_report,
)


class Command(BaseCommand):
    help = "对数据库全部新闻执行只读两段式 ETH 研究过滤与事实提取，并生成临时 JSON 报告"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=6)
        parser.add_argument("--output", type=Path, required=True)

    def handle(self, *args, **options):
        try:
            report = run_fact_validation(
                batch_size=options["batch_size"],
            )
            output = write_report(report, options["output"])
        except (FactExtractionError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        stats = report["statistics"]
        self.stdout.write(
            self.style.SUCCESS(
                f"processed={stats['total_processed']} "
                f"extracted={stats['final_event_extraction_count']} "
                f"filtered={stats['filtered_count']} "
                f"output={output}"
            )
        )
