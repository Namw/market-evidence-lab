from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import django.core.validators
import django.db.models.deletion
import apps.scheduling.models
from django.db import migrations, models
from django.utils import timezone


BUILT_IN_SCHEDULE_NAME = "ETHUSDT每日K线采集与数据质量检查"


def create_builtin_schedule(apps, schema_editor):
    KlineSchedule = apps.get_model("scheduling", "KlineSchedule")
    run_time = time(8, 5)
    zone = ZoneInfo("Asia/Shanghai")
    now = timezone.now().astimezone(zone)
    candidate = datetime.combine(now.date(), run_time, tzinfo=zone)
    if candidate <= now:
        candidate += timedelta(days=1)
    KlineSchedule.objects.get_or_create(
        name=BUILT_IN_SCHEDULE_NAME,
        defaults={
            "enabled": False,
            "run_time": run_time,
            "timezone": "Asia/Shanghai",
            "lookback_days": 3,
            "next_run_at": candidate.astimezone(UTC),
        },
    )


def remove_builtin_schedule(apps, schema_editor):
    KlineSchedule = apps.get_model("scheduling", "KlineSchedule")
    KlineSchedule.objects.filter(name=BUILT_IN_SCHEDULE_NAME).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="KlineSchedule",
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
                ("run_time", models.TimeField(default=time(8, 5))),
                (
                    "timezone",
                    models.CharField(
                        default="Asia/Shanghai",
                        editable=False,
                        max_length=64,
                    ),
                ),
                (
                    "lookback_days",
                    models.PositiveSmallIntegerField(
                        default=3,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(30),
                        ],
                    ),
                ),
                ("next_run_at", models.DateTimeField()),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="SchedulerHeartbeat",
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
                ("executor_id", models.CharField(max_length=64, unique=True)),
                ("is_running", models.BooleanField(default=True)),
                ("poll_interval_seconds", models.PositiveIntegerField(default=30)),
                ("started_at", models.DateTimeField()),
                ("last_heartbeat_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-last_heartbeat_at"],
                "indexes": [
                    models.Index(
                        fields=["-last_heartbeat_at"],
                        name="scheduler_heartbeat_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="WorkflowRun",
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
                (
                    "trigger",
                    models.CharField(
                        choices=[("scheduled", "定时"), ("manual", "手工")],
                        max_length=20,
                    ),
                ),
                ("range_start", models.DateTimeField()),
                ("range_end", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "运行中"),
                            ("success", "成功"),
                            ("partial", "部分完成"),
                            ("failed", "失败"),
                        ],
                        default="running",
                        max_length=20,
                    ),
                ),
                (
                    "quality_status",
                    models.CharField(
                        choices=[
                            ("pending", "待判定"),
                            ("passed", "通过"),
                            ("issues", "发现问题"),
                            ("unknown", "未知"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "details",
                    models.JSONField(default=apps.scheduling.models.empty_workflow_details),
                ),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "schedule",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="workflow_runs",
                        to="scheduling.klineschedule",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
                "indexes": [
                    models.Index(
                        fields=["-started_at"],
                        name="workflow_started_desc_idx",
                    ),
                    models.Index(
                        fields=["schedule", "-started_at"],
                        name="workflow_sched_start_idx",
                    ),
                ],
            },
        ),
        migrations.RunPython(create_builtin_schedule, remove_builtin_schedule),
    ]
