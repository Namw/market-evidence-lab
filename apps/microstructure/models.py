from django.db import models


class MarketMinute(models.Model):
    """One display-ready minute built from Binance trade and depth streams."""

    symbol = models.CharField(max_length=20)
    minute_start = models.DateTimeField()
    minute_end = models.DateTimeField()

    open_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    high_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    low_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    close_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    future_5m_return = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
        help_text="严格连续五分钟后的收盘价相对当前收盘价收益。",
    )
    quote_volume = models.DecimalField(max_digits=40, decimal_places=18, default=0)
    taker_buy_quote = models.DecimalField(max_digits=40, decimal_places=18, default=0)
    taker_sell_quote = models.DecimalField(max_digits=40, decimal_places=18, default=0)
    delta_quote = models.DecimalField(max_digits=40, decimal_places=18, default=0)
    trade_count = models.PositiveIntegerField(default=0)
    first_trade_id = models.PositiveBigIntegerField(null=True, blank=True)
    last_trade_id = models.PositiveBigIntegerField(null=True, blank=True)
    kline_closed = models.BooleanField(default=False)

    bid_depth_open = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    bid_depth_close = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    bid_depth_mean = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    bid_depth_sum = models.DecimalField(max_digits=50, decimal_places=18, default=0)
    ask_depth_open = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    ask_depth_close = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    ask_depth_mean = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    ask_depth_sum = models.DecimalField(max_digits=50, decimal_places=18, default=0)
    imbalance_top5_close = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    imbalance_top5_mean = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    imbalance_top5_sum = models.DecimalField(
        max_digits=50, decimal_places=18, default=0
    )
    imbalance_top5_sample_count = models.PositiveSmallIntegerField(default=0)
    spread_bps_mean = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    spread_bps_p95 = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    spread_bps_sum = models.DecimalField(max_digits=40, decimal_places=18, default=0)
    spread_bps_samples = models.JSONField(default=list, blank=True)
    book_sample_count = models.PositiveSmallIntegerField(default=0)
    coverage_ratio = models.DecimalField(max_digits=7, decimal_places=6, default=0)
    first_book_sample_at = models.DateTimeField(null=True, blank=True)
    last_book_sample_at = models.DateTimeField(null=True, blank=True)

    latest_event_time = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["minute_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "minute_start"],
                name="unique_market_minute",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "-minute_start"],
                name="market_minute_symbol_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:1m:{self.minute_start.isoformat()}"


class MicrostructureCollectorRun(models.Model):
    class Status(models.TextChoices):
        STARTING = "starting", "启动中"
        RUNNING = "running", "运行中"
        STOPPING = "stopping", "停止中"
        STOPPED = "stopped", "已停止"
        FAILED = "failed", "异常"

    class ConnectionState(models.TextChoices):
        CONNECTING = "connecting", "连接中"
        CONNECTED = "connected", "已连接"
        RECONNECTING = "reconnecting", "重连中"
        DISCONNECTED = "disconnected", "未连接"

    symbol = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.STARTING
    )
    connection_state = models.CharField(
        max_length=20,
        choices=ConnectionState.choices,
        default=ConnectionState.CONNECTING,
    )
    process_id = models.PositiveIntegerField(null=True, blank=True)
    oi_process_id = models.PositiveIntegerField(null=True, blank=True)
    received_messages = models.PositiveBigIntegerField(default=0)
    saved_minute_updates = models.PositiveBigIntegerField(default=0)
    reconnect_count = models.PositiveIntegerField(default=0)
    latest_event_time = models.DateTimeField(null=True, blank=True)
    latest_sampled_at = models.DateTimeField(null=True, blank=True)
    latest_update_id = models.PositiveBigIntegerField(null=True, blank=True)
    latest_bids = models.JSONField(default=list, blank=True)
    latest_asks = models.JSONField(default=list, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=1_000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="micro_run_created_idx"),
            models.Index(fields=["status"], name="micro_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:{self.status}:#{self.pk}"


class MarketPilotRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    symbol = models.CharField(max_length=20)
    prompt_version = models.CharField(max_length=80)
    configured_model = models.CharField(max_length=160)
    actual_models = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    window_count = models.PositiveIntegerField(default=0)
    request_count = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    future_outcomes_excluded = models.BooleanField(default=True)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["symbol", "-started_at"], name="pilot_run_sym_start_idx")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol"],
                condition=models.Q(status="running"),
                name="pilot_one_running_symbol",
            )
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:{self.status}:#{self.pk}"


class MarketPilotReport(models.Model):
    class SelectionReason(models.TextChoices):
        SHOCK = "absolute_return_ge_2pct", "候选异动"
        CALM = "calm_control", "平静对照"

    class Mechanism(models.TextChoices):
        TREND_EXPANSION = "trend_expansion", "趋势扩张"
        SHORT_SQUEEZE = "short_squeeze", "空头回补"
        LONG_LIQUIDATION = "long_liquidation", "多头去杠杆"
        TECHNICAL_REBOUND = "technical_rebound", "技术反弹"
        TECHNICAL_PULLBACK = "technical_pullback", "技术回调"
        LIQUIDITY_JUMP = "liquidity_jump", "流动性跳变"
        MIXED = "mixed", "混合机制"
        INSUFFICIENT = "insufficient_evidence", "证据不足"

    class Confidence(models.TextChoices):
        LOW = "low", "低"
        MEDIUM = "medium", "中"
        HIGH = "high", "高"

    run = models.ForeignKey(
        MarketPilotRun, on_delete=models.PROTECT, related_name="reports"
    )
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    selection_reason = models.CharField(
        max_length=40, choices=SelectionReason.choices
    )
    mechanism = models.CharField(max_length=40, choices=Mechanism.choices)
    confidence = models.CharField(max_length=20, choices=Confidence.choices)
    input_snapshot = models.JSONField()
    ai_analysis = models.JSONField()
    future_outcomes = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-window_start", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "window_start"], name="pilot_report_run_window_unique"
            )
        ]
        indexes = [
            models.Index(
                fields=["run", "-window_start"], name="pilot_report_run_time_idx"
            ),
            models.Index(
                fields=["mechanism", "confidence"], name="pilot_report_mech_conf_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run.symbol}:{self.window_start.isoformat()}:{self.mechanism}"
