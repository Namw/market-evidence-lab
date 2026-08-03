from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations, models
from django.utils import timezone


SCHEDULE_TIMEZONE = "Asia/Shanghai"
CORE_NAME = "官方与监管新闻每日采集、质量检查与增量分析"
COINDESK_NAME = "CoinDesk 每6小时采集、质量检查与增量分析"


def next_run_at(run_time, interval_hours):
    schedule_zone = ZoneInfo(SCHEDULE_TIMEZONE)
    local_current = timezone.now().astimezone(schedule_zone)
    candidate = datetime.combine(
        local_current.date(),
        run_time,
        tzinfo=schedule_zone,
    )
    if candidate <= local_current:
        elapsed_seconds = (local_current - candidate).total_seconds()
        interval_seconds = interval_hours * 60 * 60
        steps = int(elapsed_seconds // interval_seconds) + 1
        candidate += timedelta(hours=steps * interval_hours)
    return candidate.astimezone(UTC)


def split_news_schedules(apps, schema_editor):
    NewsWorkflowSchedule = apps.get_model("scheduling", "NewsWorkflowSchedule")
    core = NewsWorkflowSchedule.objects.order_by("pk").first()
    if core is None:
        core = NewsWorkflowSchedule.objects.create(
            name=CORE_NAME,
            feed_group="core",
            enabled=False,
            interval_hours=24,
            run_time=time(8, 35),
            timezone=SCHEDULE_TIMEZONE,
            next_run_at=next_run_at(time(8, 35), 24),
        )
    else:
        core.name = CORE_NAME
        core.feed_group = "core"
        core.interval_hours = 24
        core.next_run_at = next_run_at(core.run_time, 24)
        core.save(
            update_fields=[
                "name",
                "feed_group",
                "interval_hours",
                "next_run_at",
                "updated_at",
            ]
        )
    NewsWorkflowSchedule.objects.get_or_create(
        feed_group="coindesk",
        defaults={
            "name": COINDESK_NAME,
            "enabled": False,
            "interval_hours": 6,
            "run_time": time(8, 35),
            "timezone": SCHEDULE_TIMEZONE,
            "next_run_at": next_run_at(time(8, 35), 6),
        },
    )


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0004_news_workflow_six_hour_cadence")]

    operations = [
        migrations.AddField(
            model_name="newsworkflowschedule",
            name="feed_group",
            field=models.CharField(
                choices=[
                    ("core", "官方与监管新闻"),
                    ("coindesk", "CoinDesk 新闻"),
                ],
                default="core",
                max_length=20,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="newsworkflowschedule",
            name="interval_hours",
            field=models.PositiveSmallIntegerField(default=24),
        ),
        migrations.AddField(
            model_name="newsworkflowrun",
            name="feed_group",
            field=models.CharField(
                choices=[
                    ("core", "官方与监管新闻"),
                    ("coindesk", "CoinDesk 新闻"),
                ],
                default="core",
                max_length=20,
            ),
        ),
        migrations.RunPython(split_news_schedules, migrations.RunPython.noop),
    ]
