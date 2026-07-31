from django.db import models


class CollectionRun(models.Model):
    class DataType(models.TextChoices):
        KLINE = "kline", "K线"

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
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"

    data_type = models.CharField(
        max_length=20,
        choices=DataType.choices,
        default=DataType.KLINE,
    )
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
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    request_count = models.PositiveIntegerField(default=0)
    received_count = models.PositiveIntegerField(default=0)
    inserted_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"], name="run_started_desc_idx"),
            models.Index(
                fields=["symbol", "interval", "-started_at"],
                name="run_sym_int_started_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:{self.interval}:{self.status}:{self.started_at.isoformat()}"
