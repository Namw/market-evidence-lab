import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class MemeMonitorRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        STOPPED = "stopped", "已停止"
        FAILED = "failed", "失败"

    class Mode(models.TextChoices):
        CONTINUOUS = "continuous", "常驻监听"
        ONCE = "once", "单轮执行"

    source = models.CharField(max_length=40, default="geckoterminal")
    chain = models.CharField(max_length=32)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    process_id = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField()
    stopped_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField()
    cycle_count = models.PositiveIntegerField(default=0)
    successful_cycle_count = models.PositiveIntegerField(default=0)
    failed_cycle_count = models.PositiveIntegerField(default=0)
    latest_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(
                fields=["chain", "-started_at"], name="meme_run_chain_start_idx"
            ),
            models.Index(
                fields=["status", "-heartbeat_at"], name="meme_run_status_beat_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.chain}:{self.mode}:{self.status}:#{self.pk}"


class MemeMonitorSchedule(models.Model):
    name = models.CharField(max_length=120, unique=True)
    enabled = models.BooleanField(default=False)
    interval_seconds = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(3600)],
    )
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class MemeMonitorCycle(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCEEDED = "succeeded", "成功"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"

    run = models.ForeignKey(
        MemeMonitorRun,
        on_delete=models.PROTECT,
        related_name="cycles",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    fetched_pairs = models.PositiveIntegerField(default=0)
    tracked_pairs = models.PositiveIntegerField(default=0)
    saved_snapshots = models.PositiveIntegerField(default=0)
    detected_anomalies = models.PositiveIntegerField(default=0)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(
                fields=["run", "-started_at"], name="meme_cycle_run_start_idx"
            ),
            models.Index(
                fields=["status", "-started_at"], name="meme_cycle_status_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"run#{self.run_id}:{self.status}:{self.started_at.isoformat()}"


class MemeMarketSnapshot(models.Model):
    source = models.CharField(max_length=40, default="geckoterminal")
    chain = models.CharField(max_length=32)
    dex = models.CharField(max_length=80)
    token_address = models.CharField(max_length=128)
    pair_address = models.CharField(max_length=128)
    symbol = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=300, blank=True)
    pair_created_at = models.DateTimeField()
    price_usd = models.DecimalField(
        max_digits=50, decimal_places=24, null=True, blank=True
    )
    liquidity_usd = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    market_cap = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    fdv = models.DecimalField(max_digits=50, decimal_places=8, null=True, blank=True)
    volume_5m = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    volume_1h = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    buys_5m = models.PositiveIntegerField(null=True, blank=True)
    sells_5m = models.PositiveIntegerField(null=True, blank=True)
    price_change_5m = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    price_change_1h = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    timestamp = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp", "pair_address"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chain", "pair_address", "timestamp"],
                name="meme_snapshot_source_pair_time_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["chain", "pair_address", "-timestamp"],
                name="meme_snapshot_pair_time_idx",
            ),
            models.Index(
                fields=["chain", "-pair_created_at"],
                name="meme_snapshot_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.chain}:{self.symbol}:{self.timestamp.isoformat()}"


class MemeAnomalyEventRecord(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(
        MemeMarketSnapshot,
        on_delete=models.PROTECT,
        related_name="anomaly_events",
    )
    anomaly_type = models.CharField(max_length=80, default="market_spike")
    event_time = models.DateTimeField()
    chain = models.CharField(max_length=32)
    token_address = models.CharField(max_length=128)
    pair_address = models.CharField(max_length=128)
    symbol = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=300, blank=True)
    pair_age_minutes = models.PositiveIntegerField()
    price_usd = models.DecimalField(
        max_digits=50, decimal_places=24, null=True, blank=True
    )
    price_change_5m = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    price_change_1h = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    volume_5m = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    liquidity_usd = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    buys_5m = models.PositiveIntegerField(null=True, blank=True)
    sells_5m = models.PositiveIntegerField(null=True, blank=True)
    triggered_rules = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_time", "-created_at"]
        indexes = [
            models.Index(
                fields=["chain", "token_address", "anomaly_type", "-event_time"],
                name="meme_event_cooldown_idx",
            ),
            models.Index(fields=["-event_time"], name="meme_event_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.chain}:{self.symbol}:{self.anomaly_type}:{self.event_time.isoformat()}"
