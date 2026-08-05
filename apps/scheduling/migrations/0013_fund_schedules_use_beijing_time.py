from datetime import time

from django.db import migrations, models


SCHEDULE_TIMEZONE = "Asia/Shanghai"
BEIJING_RUN_TIMES = {
    "stablecoin": (time(14, 0), None),
    "etf": (time(14, 0), time(20, 0)),
    "addresses": (time(8, 10), None),
}


def convert_fund_schedule_wall_times(apps, schema_editor):
    FundDataSchedule = apps.get_model("scheduling", "FundDataSchedule")
    for task_type, (run_time, supplement_run_time) in BEIJING_RUN_TIMES.items():
        FundDataSchedule.objects.filter(task_type=task_type).update(
            run_time=run_time,
            supplement_run_time=supplement_run_time,
            timezone=SCHEDULE_TIMEZONE,
        )


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0012_remove_news_proxy_flags")]

    operations = [
        migrations.AlterField(
            model_name="funddataschedule",
            name="timezone",
            field=models.CharField(
                default=SCHEDULE_TIMEZONE,
                editable=False,
                max_length=64,
            ),
        ),
        migrations.RunPython(
            convert_fund_schedule_wall_times,
            migrations.RunPython.noop,
        ),
    ]
