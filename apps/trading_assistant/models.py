import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=100, default="新的开仓分析")
    symbol = models.CharField(max_length=20)
    horizon_minutes = models.PositiveSmallIntegerField(default=240)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class MarketSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    symbol = models.CharField(max_length=20)
    cutoff = models.DateTimeField()
    captured_at = models.DateTimeField(auto_now_add=True)
    quality = models.JSONField(default=dict)
    rows = models.JSONField(default=list, encoder=DjangoJSONEncoder)
    calculation_version = models.CharField(max_length=40)


class AnalysisTurn(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "等待分析"
        RUNNING = "running", "分析中"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "未完成"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="turns")
    request_id = models.UUIDField()
    question = models.TextField()
    horizon_minutes = models.PositiveSmallIntegerField(default=240)
    refresh_data = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    progress = models.CharField(max_length=180, default="等待后台分析进程领取")
    snapshot = models.ForeignKey(MarketSnapshot, null=True, blank=True, on_delete=models.PROTECT)
    report = models.JSONField(default=dict)
    input_context = models.JSONField(default=dict)
    safe_error = models.CharField(max_length=300, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    prompt_hash = models.CharField(max_length=64, blank=True)
    prompt_text = models.TextField(blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    usage = models.JSONField(default=dict)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "request_id"], name="assistant_request_once"),
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(status__in=["queued", "running"]),
                name="assistant_one_active_turn",
            ),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="assistant_turn_queue")]


class ToolExecution(models.Model):
    turn = models.ForeignKey(AnalysisTurn, on_delete=models.CASCADE, related_name="tool_executions")
    cache_key = models.CharField(max_length=64)
    name = models.CharField(max_length=80)
    arguments = models.JSONField(default=dict)
    result = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["turn", "cache_key"], name="assistant_tool_once")]


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=50, primary_key=True)
    seen_at = models.DateTimeField()
