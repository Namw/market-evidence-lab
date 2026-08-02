from django.core.management.base import BaseCommand, CommandError

from apps.news_analysis.models import NewsAnalysisRun
from apps.news_analysis.services import AnalysisAlreadyRunning, run_news_analysis


class Command(BaseCommand):
    help = "运行 ETH 新闻分类（无关及超过 3 天的模糊记录会按规则删除）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=[
                NewsAnalysisRun.Mode.INCREMENTAL,
                NewsAnalysisRun.Mode.RETRY_FAILED,
                NewsAnalysisRun.Mode.SMOKE,
            ],
            default=NewsAnalysisRun.Mode.INCREMENTAL,
        )
        parser.add_argument("--news-id", type=int)

    def handle(self, *args, **options):
        mode = options["mode"]
        news_id = options.get("news_id")
        if mode == NewsAnalysisRun.Mode.SMOKE and not news_id:
            raise CommandError("smoke 模式必须提供 --news-id。")
        if mode != NewsAnalysisRun.Mode.SMOKE and news_id:
            raise CommandError("--news-id 仅用于 smoke 模式。")
        try:
            run = run_news_analysis(
                mode=mode,
                trigger=NewsAnalysisRun.Trigger.COMMAND,
                record_ids=[news_id] if news_id else None,
            )
        except AnalysisAlreadyRunning as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"run={run.id} status={run.status} candidate={run.candidate_count} "
                f"success={run.success_count} failed={run.failure_count} "
                f"skipped={run.skipped_count} api_requests={run.api_request_count}"
            )
        )
