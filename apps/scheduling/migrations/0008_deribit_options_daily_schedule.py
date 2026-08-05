from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations, models
from django.utils import timezone


SCHEDULE_TIMEZONE = "Asia/Shanghai"
DEFAULT_RUN_TIME = time(8, 20)


def configure_daily_schedule(apps, schema_editor):
    Schedule = apps.get_model("scheduling", "DeribitOptionsSchedule")
    current = timezone.now()
    schedule_zone = ZoneInfo(SCHEDULE_TIMEZONE)
    local_current = current.astimezone(schedule_zone)
    candidate = datetime.combine(
        local_current.date(),
        DEFAULT_RUN_TIME,
        tzinfo=schedule_zone,
    )
    if candidate <= local_current:
        candidate += timedelta(days=1)
    Schedule.objects.update(
        name="Deribit ETH期权数据采集",
        run_time=DEFAULT_RUN_TIME,
        timezone=SCHEDULE_TIMEZONE,
        next_run_at=candidate.astimezone(UTC),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0007_deribitoptionsschedule_deribitoptionsworkflowrun"),
    ]

    operations = [
        migrations.AddField(
            model_name="deribitoptionsschedule",
            name="run_time",
            field=models.TimeField(default=DEFAULT_RUN_TIME),
        ),
        migrations.AddField(
            model_name="deribitoptionsschedule",
            name="timezone",
            field=models.CharField(
                default=SCHEDULE_TIMEZONE,
                editable=False,
                max_length=64,
            ),
        ),
        migrations.RunPython(configure_daily_schedule, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="deribitoptionsschedule",
            name="interval_minutes",
        ),
    ]
