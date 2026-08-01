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


class NewsWorkflowSchedule(models.Model):
    name = models.CharField(max_length=120, unique=True)
    enabled = models.BooleanField(default=False)
    run_time = models.TimeField(default=time(8, 35))
    timezone = models.CharField(max_length=64, default=SCHEDULE_TIMEZONE, editable=False)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class NewsWorkflowRun(models.Model):
    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时"
        MANUAL = "manual", "手工"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"

    class StepStatus(models.TextChoices):
        PENDING = "pending", "待执行"
        SUCCESS = "success", "成功"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"
        NOT_RUN = "not_run", "未执行"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "待检查"
        PASSED = "passed", "通过"
        WARNING = "warning", "警告"
        FAILED = "failed", "失败"
        NOT_RUN = "not_run", "未执行"

    schedule = models.ForeignKey(
        NewsWorkflowSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_runs",
    )
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    ethereum_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ethereum_news_workflows",
    )
    ethereum_collection_status = models.CharField(
        max_length=20,
        choices=StepStatus.choices,
        default=StepStatus.PENDING,
    )
    binance_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="binance_news_workflows",
    )
    binance_collection_status = models.CharField(
        max_length=20,
        choices=StepStatus.choices,
        default=StepStatus.PENDING,
    )
    ethereum_inspection_run = models.ForeignKey(
        "inspection.NewsInspectionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ethereum_news_workflows",
    )
    ethereum_quality_status = models.CharField(
        max_length=20,
        choices=QualityStatus.choices,
        default=QualityStatus.PENDING,
    )
    binance_inspection_run = models.ForeignKey(
        "inspection.NewsInspectionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="binance_news_workflows",
    )
    binance_quality_status = models.CharField(
        max_length=20,
        choices=QualityStatus.choices,
        default=QualityStatus.PENDING,
    )
    analysis_run = models.ForeignKey(
        "news_analysis.NewsAnalysisRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="news_workflows",
    )
    analysis_status = models.CharField(
        max_length=20,
        choices=StepStatus.choices,
        default=StepStatus.PENDING,
    )
    inserted_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    quality_issue_count = models.PositiveIntegerField(default=0)
    analysis_candidate_count = models.PositiveIntegerField(default=0)
    analysis_success_count = models.PositiveIntegerField(default=0)
    analysis_failure_count = models.PositiveIntegerField(default=0)
    analysis_skipped_count = models.PositiveIntegerField(default=0)
    safe_error_summary = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="running"),
                name="news_workflow_one_running",
            )
        ]
        indexes = [
            models.Index(
                fields=["-started_at"],
                name="news_workflow_start_idx",
            ),
            models.Index(
                fields=["schedule", "-started_at"],
                name="news_workflow_sched_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.trigger}:{self.status}:{self.started_at.isoformat()}"
