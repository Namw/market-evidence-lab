from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.meme_monitor.models import (
    MemeAnomalyEventRecord,
    MemeContinuationResearchEpisode,
    MemeMarketSnapshot,
)


class Command(BaseCommand):
    help = (
        "Remove pre-cutover raw Meme snapshots and their legacy anomaly records. "
        "Defaults to a dry run; current pair state is never touched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--before",
            required=True,
            help="Exclusive ISO-8601 cutover time, for example 2026-08-31T22:30:00+08:00.",
        )
        parser.add_argument("--confirm", action="store_true", help="Actually delete.")
        parser.add_argument("--batch-size", type=int, default=5000)

    def handle(self, *args, **options):
        before = parse_datetime(options["before"])
        if before is None or before.tzinfo is None:
            raise CommandError("--before must be a timezone-aware ISO-8601 datetime.")
        batch_size = options["batch_size"]
        if not 1 <= batch_size <= 50_000:
            raise CommandError("--batch-size must be between 1 and 50000.")

        snapshots = MemeMarketSnapshot.objects.filter(timestamp__lt=before)
        event_ids = list(
            MemeAnomalyEventRecord.objects.filter(snapshot__timestamp__lt=before)
            .values_list("event_id", flat=True)
        )
        episode_count = MemeContinuationResearchEpisode.objects.filter(
            trigger_event_id__in=event_ids
        ).count()
        summary = (
            f"before={before.isoformat()} snapshots={snapshots.count()} "
            f"legacy_events={len(event_ids)} related_episodes={episode_count}"
        )
        if not options["confirm"]:
            self.stdout.write(f"DRY RUN: {summary}")
            self.stdout.write("Re-run with --confirm after the new collector is deployed.")
            return

        deleted = {"episodes": 0, "events": 0, "snapshots": 0}
        while True:
            snapshot_ids = list(
                snapshots.order_by("id").values_list("id", flat=True)[:batch_size]
            )
            if not snapshot_ids:
                break
            with transaction.atomic():
                batch_event_ids = list(
                    MemeAnomalyEventRecord.objects.filter(snapshot_id__in=snapshot_ids)
                    .values_list("event_id", flat=True)
                )
                episodes, _ = MemeContinuationResearchEpisode.objects.filter(
                    trigger_event_id__in=batch_event_ids
                ).delete()
                events, _ = MemeAnomalyEventRecord.objects.filter(
                    event_id__in=batch_event_ids
                ).delete()
                raw, _ = MemeMarketSnapshot.objects.filter(id__in=snapshot_ids).delete()
            deleted["episodes"] += episodes
            deleted["events"] += events
            deleted["snapshots"] += raw
        self.stdout.write(self.style.SUCCESS(f"Purged: {summary}; deleted={deleted}"))
