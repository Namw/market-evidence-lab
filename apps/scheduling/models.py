from datetime import time

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


SCHEDULE_TIMEZONE = "Asia/Shanghai"


def empty_workflow_details():
    return {
        "collection_1d_run_id": None,
        "inspection_1d_run_id": None,
        "collection_1h_run_id": None,
        "inspection_1h_run_id": None,
        "collection_oi_run_id": None,
        "inspection_oi_run_id": None,
        "collection_funding_run_id": None,
        "inspection_funding_run_id": None,
        "steps": {
            "collection_1d": {"status": "pending", "error_summary": ""},
            "inspection_1d": {"status": "pending", "error_summary": ""},
            "collection_1h": {"status": "pending", "error_summary": ""},
            "inspection_1h": {"status": "pending", "error_summary": ""},
            "collection_oi": {"status": "pending", "error_summary": ""},
            "inspection_oi": {"status": "pending", "error_summary": ""},
            "collection_funding": {"status": "pending", "error_summary": ""},
            "inspection_funding": {"status": "pending", "error_summary": ""},
        },
    }


class KlineSchedule(models.Model):
    name = models.CharField(max_length=120, unique=True)
    enabled = models.BooleanField(default=False)
    run_time = models.TimeField(default=time(8, 5))
    timezone = models.CharField(max_length=64, default=SCHEDULE_TIMEZONE, editable=False)
    lookback_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class WorkflowRun(models.Model):
    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时"
        MANUAL = "manual", "手工"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        PARTIAL = "partial", "部分完成"
        FAILED = "failed", "失败"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "待判定"
        PASSED = "passed", "通过"
        ISSUES = "issues", "发现问题"
        UNKNOWN = "unknown", "未知"

    schedule = models.ForeignKey(
        KlineSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_runs",
    )
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    quality_status = models.CharField(
        max_length=20,
        choices=QualityStatus.choices,
        default=QualityStatus.PENDING,
    )
    details = models.JSONField(default=empty_workflow_details)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"], name="workflow_started_desc_idx"),
            models.Index(
                fields=["schedule", "-started_at"],
                name="workflow_sched_start_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.trigger}:{self.status}:{self.started_at.isoformat()}"


class SchedulerHeartbeat(models.Model):
    executor_id = models.CharField(max_length=64, unique=True)
    is_running = models.BooleanField(default=True)
    poll_interval_seconds = models.PositiveIntegerField(default=30)
    started_at = models.DateTimeField()
    last_heartbeat_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_heartbeat_at"]
        indexes = [
            models.Index(
                fields=["-last_heartbeat_at"],
                name="scheduler_heartbeat_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.executor_id}:{self.last_heartbeat_at.isoformat()}"
