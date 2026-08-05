from django.core.management.base import BaseCommand, CommandError

from apps.news_analysis.event_merge import (
    EventMergeAlreadyRunning,
    retry_failed_event_pairs,
    run_event_merge,
)
from apps.news_analysis.models import EventMergeRun


class Command(BaseCommand):
    help = "重建暂定新闻事件库，或基于冻结输入重试某次运行的技术失败项"

    def add_arguments(self, parser):
        parser.add_argument("--retry-run", type=int)

    def handle(self, *args, **options):
        try:
            if options.get("retry_run"):
                original = EventMergeRun.objects.get(pk=options["retry_run"])
                run = retry_failed_event_pairs(original)
            else:
                run = run_event_merge(trigger=EventMergeRun.Trigger.FULL_REBUILD)
        except EventMergeRun.DoesNotExist as exc:
            raise CommandError("指定的原运行不存在。") from exc
        except (EventMergeAlreadyRunning, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"run={run.id} status={run.status} input={run.input_count} "
            f"eligible={run.eligible_count} candidates={run.candidate_pair_count} "
            f"ai={run.ai_decision_count} failures={run.ai_failure_count} "
            f"events={run.final_event_count} current={run.is_current_snapshot}"
        )
        if run.status == EventMergeRun.Status.FAILED:
            raise CommandError(run.safe_error_summary or "事件库构建失败。")
