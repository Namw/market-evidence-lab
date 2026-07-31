from django.db import models


def empty_inspection_details():
    return {
        "missing_ranges": [],
        "duplicate_open_times": [],
        "misaligned_open_times": [],
        "invalid_rows": [],
        "details_truncated": False,
    }


class KlineInspectionRun(models.Model):
    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"

    class Interval(models.TextChoices):
        ONE_DAY = "1d", "1d"
        ONE_HOUR = "1h", "1h"

    class Trigger(models.TextChoices):
        MANUAL = "manual", "手工"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "待判定"
        PASSED = "passed", "通过"
        ISSUES = "issues", "发现问题"

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
    expected_count = models.PositiveIntegerField(default=0)
    actual_count = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    misaligned_count = models.PositiveIntegerField(default=0)
    invalid_ohlc_count = models.PositiveIntegerField(default=0)
    invalid_numeric_count = models.PositiveIntegerField(default=0)
    invalid_close_time_count = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=empty_inspection_details)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"], name="inspect_started_desc_idx"),
            models.Index(
                fields=["symbol", "interval", "-started_at"],
                name="inspect_sym_int_start_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.symbol}:{self.interval}:{self.status}:"
            f"{self.quality_status}:{self.started_at.isoformat()}"
        )
