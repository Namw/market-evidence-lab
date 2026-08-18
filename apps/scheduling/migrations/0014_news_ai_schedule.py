import datetime

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("news_analysis", "0008_eventmergerun_canonicalevent_eventpairdecision_and_more"),
        ("scheduling", "0013_fund_schedules_use_beijing_time"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsAISchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("run_time", models.TimeField(default=datetime.time(3, 30))),
                ("timezone", models.CharField(default="Asia/Shanghai", editable=False, max_length=64)),
                ("max_direction_requests", models.PositiveSmallIntegerField(default=50, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(500)])),
                ("max_objective_records", models.PositiveSmallIntegerField(default=50, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(1000)])),
                ("max_event_ai_calls", models.PositiveSmallIntegerField(default=100, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1000)])),
                ("next_run_at", models.DateTimeField()),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="NewsAIWorkflowRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("trigger", models.CharField(choices=[("scheduled", "定时"), ("manual", "手工")], max_length=20)),
                ("status", models.CharField(choices=[("running", "运行中"), ("success", "成功"), ("partial", "部分成功"), ("failed", "失败")], default="running", max_length=20)),
                ("analysis_status", models.CharField(choices=[("pending", "待执行"), ("success", "成功"), ("partial", "部分成功"), ("failed", "失败"), ("not_run", "未执行")], default="pending", max_length=20)),
                ("objective_fact_status", models.CharField(choices=[("pending", "待执行"), ("success", "成功"), ("partial", "部分成功"), ("failed", "失败"), ("not_run", "未执行")], default="pending", max_length=20)),
                ("event_merge_status", models.CharField(choices=[("pending", "待执行"), ("success", "成功"), ("partial", "部分成功"), ("failed", "失败"), ("not_run", "未执行")], default="pending", max_length=20)),
                ("max_direction_requests", models.PositiveSmallIntegerField(default=50)),
                ("max_objective_records", models.PositiveSmallIntegerField(default=50)),
                ("max_event_ai_calls", models.PositiveSmallIntegerField(default=100)),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("total_tokens", models.PositiveIntegerField(default=0)),
                ("safe_error_summary", models.CharField(blank=True, max_length=1000)),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("analysis_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="news_ai_workflows", to="news_analysis.newsanalysisrun")),
                ("event_merge_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="news_ai_workflows", to="news_analysis.eventmergerun")),
                ("objective_fact_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="news_ai_workflows", to="news_analysis.objectivefactextractionrun")),
                ("schedule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="workflow_runs", to="scheduling.newsaischedule")),
            ],
            options={"ordering": ["-started_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="newsaiworkflowrun",
            index=models.Index(fields=["-started_at"], name="news_ai_workflow_start_idx"),
        ),
        migrations.AddConstraint(
            model_name="newsaiworkflowrun",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "running")), fields=("status",), name="news_ai_workflow_one_running"),
        ),
    ]
