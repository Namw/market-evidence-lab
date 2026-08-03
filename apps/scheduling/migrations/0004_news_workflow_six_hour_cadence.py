from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone


SCHEDULE_TIMEZONE = "Asia/Shanghai"
INTERVAL_HOURS = 6


def update_news_schedule_cadence(apps, schema_editor):
    NewsWorkflowSchedule = apps.get_model("scheduling", "NewsWorkflowSchedule")
    schedule_zone = ZoneInfo(SCHEDULE_TIMEZONE)
    current = timezone.now()
    local_current = current.astimezone(schedule_zone)
    for schedule in NewsWorkflowSchedule.objects.all():
        candidate = datetime.combine(
            local_current.date(),
            schedule.run_time,
            tzinfo=schedule_zone,
        )
        if candidate <= local_current:
            elapsed_seconds = (local_current - candidate).total_seconds()
            interval_seconds = INTERVAL_HOURS * 60 * 60
            steps = int(elapsed_seconds // interval_seconds) + 1
            candidate += timedelta(hours=steps * INTERVAL_HOURS)
        schedule.next_run_at = candidate.astimezone(UTC)
        schedule.save(update_fields=["next_run_at", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0003_newsworkflowfeedrun")]

    operations = [
        migrations.RunPython(update_news_schedule_cadence, migrations.RunPython.noop),
    ]
