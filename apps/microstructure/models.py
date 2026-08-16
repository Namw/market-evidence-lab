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
