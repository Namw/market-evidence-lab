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


class MemePairState(models.Model):
    """The latest observable state for a pair, plus a deliberately bounded baseline."""

    source = models.CharField(max_length=40, default="geckoterminal")
    chain = models.CharField(max_length=32)
    dex = models.CharField(max_length=80)
    token_address = models.CharField(max_length=128)
    pair_address = models.CharField(max_length=128)
    symbol = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=300, blank=True)
    pair_created_at = models.DateTimeField()
    price_usd = models.DecimalField(max_digits=50, decimal_places=24, null=True, blank=True)
    liquidity_usd = models.DecimalField(max_digits=50, decimal_places=8, null=True, blank=True)
    market_cap = models.DecimalField(max_digits=50, decimal_places=8, null=True, blank=True)
    fdv = models.DecimalField(max_digits=50, decimal_places=8, null=True, blank=True)
    volume_5m = models.DecimalField(max_digits=50, decimal_places=8, null=True, blank=True)
    volume_1h = models.DecimalField(max_digits=50, decimal_places=8, null=True, blank=True)
    buys_5m = models.PositiveIntegerField(null=True, blank=True)
    sells_5m = models.PositiveIntegerField(null=True, blank=True)
    price_change_5m = models.DecimalField(max_digits=30, decimal_places=8, null=True, blank=True)
    price_change_1h = models.DecimalField(max_digits=30, decimal_places=8, null=True, blank=True)
    # JSON is used because this is a short restart baseline, not time-series storage.
    volume_5m_history = models.JSONField(default=list)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-observed_at", "pair_address"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chain", "pair_address"],
                name="meme_pair_state_source_pair_unique",
            )
        ]
        indexes = [
            models.Index(fields=["chain", "-pair_created_at"], name="meme_state_created_idx"),
            models.Index(fields=["chain", "-observed_at"], name="meme_state_observed_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.chain}:{self.symbol}:{self.observed_at.isoformat()}"


class MemeAnomalyEventRecord(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=40, default="geckoterminal")
    snapshot = models.ForeignKey(
        MemeMarketSnapshot,
        on_delete=models.SET_NULL,
        related_name="anomaly_events",
        null=True,
        blank=True,
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


class MemeLaunchpadTokenState(models.Model):
    source = models.CharField(max_length=40, default="geckoterminal")
    chain = models.CharField(max_length=32)
    token_address = models.CharField(max_length=128)
    symbol = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=300, blank=True)
    launchpad_pair_address = models.CharField(max_length=128)
    current_pair_address = models.CharField(max_length=128)
    migrated_destination_pair_address = models.CharField(max_length=128, blank=True)
    graduation_percentage = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
    )
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-observed_at", "token_address"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chain", "token_address"],
                name="meme_launchpad_token_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["chain", "-observed_at"],
                name="meme_launchpad_seen_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.chain}:{self.symbol}:{self.current_pair_address}"


class MemeContinuationResearchEpisode(models.Model):
    class Status(models.TextChoices):
        WAITING_ENTRY = "waiting_entry", "等待可执行入场"
        WAITING_EXIT = "waiting_exit", "跟踪 5 分钟"
        COMPLETED = "completed", "研究完成"
        UNAVAILABLE = "unavailable", "无法形成样本"

    episode_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trigger_event = models.OneToOneField(
        MemeAnomalyEventRecord,
        on_delete=models.PROTECT,
        related_name="continuation_episode",
    )
    source = models.CharField(max_length=40, default="geckoterminal")
    chain = models.CharField(max_length=32)
    token_address = models.CharField(max_length=128)
    symbol = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=300, blank=True)
    rule_version = models.CharField(max_length=40, default="launchpad_5m_v1")
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.WAITING_ENTRY,
    )
    failure_reason = models.CharField(max_length=120, blank=True)
    triggered_at = models.DateTimeField()
    launchpad_pair_address = models.CharField(max_length=128)
    trigger_pair_address = models.CharField(max_length=128)
    current_pair_address = models.CharField(max_length=128)
    migrated_destination_pair_address = models.CharField(max_length=128, blank=True)
    migration_detected_at = models.DateTimeField(null=True, blank=True)
    entry_target_at = models.DateTimeField()
    entry_observed_at = models.DateTimeField(null=True, blank=True)
    entry_pair_address = models.CharField(max_length=128, blank=True)
    entry_price_usd = models.DecimalField(
        max_digits=50, decimal_places=24, null=True, blank=True
    )
    entry_liquidity_usd = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    entry_price_impact_pct = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True
    )
    exit_target_at = models.DateTimeField(null=True, blank=True)
    exit_observed_at = models.DateTimeField(null=True, blank=True)
    exit_pair_address = models.CharField(max_length=128, blank=True)
    exit_price_usd = models.DecimalField(
        max_digits=50, decimal_places=24, null=True, blank=True
    )
    exit_liquidity_usd = models.DecimalField(
        max_digits=50, decimal_places=8, null=True, blank=True
    )
    exit_price_impact_pct = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True
    )
    notional_usd = models.DecimalField(max_digits=20, decimal_places=8)
    fee_bps_per_side = models.DecimalField(max_digits=12, decimal_places=4)
    gross_return_pct = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    net_return_pct = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    cost_model = models.CharField(
        max_length=80,
        default="constant_product_v1_no_token_tax",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-triggered_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chain", "token_address", "rule_version"],
                name="meme_research_first_token_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "entry_target_at"],
                name="meme_research_entry_idx",
            ),
            models.Index(
                fields=["status", "exit_target_at"],
                name="meme_research_exit_idx",
            ),
            models.Index(
                fields=["chain", "token_address"],
                name="meme_research_token_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.chain}:{self.symbol}:{self.status}:{self.triggered_at.isoformat()}"
