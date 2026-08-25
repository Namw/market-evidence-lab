import datetime
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0014_news_ai_schedule")]

    operations = [
        migrations.CreateModel(
            name="MarketPilotSchedule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=120, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("run_time", models.TimeField(default=datetime.time(0, 10))),
                (
                    "interval_hours",
                    models.PositiveSmallIntegerField(default=4, editable=False),
                ),
                (
                    "timezone",
                    models.CharField(
                        default="Asia/Shanghai", editable=False, max_length=64
                    ),
                ),
                (
                    "threshold_pct",
                    models.DecimalField(
                        decimal_places=3, default=Decimal("2"), max_digits=6
                    ),
                ),
                ("next_run_at", models.DateTimeField()),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        )
    ]
