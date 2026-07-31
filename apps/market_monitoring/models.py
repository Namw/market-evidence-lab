from django.db import models

from apps.market_data.models import Kline


def empty_rules_snapshot():
    return {}


def empty_signals():
    return []


class MarketScanRun(models.Model):
    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"

    class Interval(models.TextChoices):
        ONE_DAY = "1d", "1d"

    class Trigger(models.TextChoices):
        MANUAL = "manual", "手工"
        SCHEDULED = "scheduled", "定时"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    exchange = models.CharField(max_length=20, choices=Exchange.choices)
    market_type = models.CharField(max_length=30, choices=MarketType.choices)
    symbol = models.CharField(max_length=20)
    interval = models.CharField(max_length=5, choices=Interval.choices)
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    trigger = models.CharField(
        max_length=20,
        choices=Trigger.choices,
        default=Trigger.MANUAL,
    )
    rules_version = models.CharField(max_length=20, default="v1")
    rules_snapshot = models.JSONField(default=empty_rules_snapshot)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    expected_count = models.PositiveIntegerField(default=0)
    actual_count = models.PositiveIntegerField(default=0)
    evaluated_count = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    skipped_invalid_count = models.PositiveIntegerField(default=0)
    volume_baseline_unavailable_count = models.PositiveIntegerField(default=0)
    anomaly_day_count = models.PositiveIntegerField(default=0)
    signal_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"], name="market_scan_started_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:{self.range_start.date()}:{self.status}"


class MarketAnomalyFinding(models.Model):
    run = models.ForeignKey(
        MarketScanRun,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    kline = models.ForeignKey(
        Kline,
        on_delete=models.PROTECT,
        related_name="market_anomaly_findings",
    )
    open_time = models.DateTimeField()
    open = models.DecimalField(max_digits=40, decimal_places=18)
    high = models.DecimalField(max_digits=40, decimal_places=18)
    low = models.DecimalField(max_digits=40, decimal_places=18)
    close = models.DecimalField(max_digits=40, decimal_places=18)
    volume = models.DecimalField(max_digits=40, decimal_places=18)
    price_change_pct = models.DecimalField(max_digits=40, decimal_places=18)
    amplitude_pct = models.DecimalField(max_digits=40, decimal_places=18)
    volume_average_20 = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    volume_ratio = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    upper_wick_body_ratio = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    upper_wick_range_ratio = models.DecimalField(max_digits=40, decimal_places=18)
    lower_wick_body_ratio = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    lower_wick_range_ratio = models.DecimalField(max_digits=40, decimal_places=18)
    signals = models.JSONField(default=empty_signals)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["open_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "open_time"],
                name="unique_market_finding_run_day",
            )
        ]
        indexes = [
            models.Index(fields=["open_time"], name="market_finding_day_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.open_time.date()}:{len(self.signals)} signals"

