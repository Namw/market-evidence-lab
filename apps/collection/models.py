from django.db import models


class CollectionRun(models.Model):
    class DataType(models.TextChoices):
        KLINE = "kline", "K线"
        OPEN_INTEREST = "open_interest", "OI"
        FUNDING = "funding", "Funding"
        DERIBIT_DVOL = "deribit_dvol", "Deribit DVOL"
        DERIBIT_OPTION_INSTRUMENT = "deribit_option_instrument", "Deribit期权合约"
        DERIBIT_OPTION_SNAPSHOT = "deribit_option_snapshot", "Deribit期权快照"
        NEWS = "news", "新闻"

    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"
        DERIBIT = "deribit", "Deribit"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"
        OPTIONS = "options", "Options"

    class Interval(models.TextChoices):
        ONE_DAY = "1d", "1d"
        ONE_HOUR = "1h", "1h"
        FIVE_MINUTES = "5m", "5m"
        ACTUAL = "actual", "实际结算"

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时"
        MANUAL = "manual", "手工"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"

    data_type = models.CharField(
        max_length=30,
        choices=DataType.choices,
        default=DataType.KLINE,
    )
    exchange = models.CharField(max_length=20, choices=Exchange.choices, blank=True)
    market_type = models.CharField(max_length=30, choices=MarketType.choices, blank=True)
    symbol = models.CharField(max_length=20, blank=True)
    interval = models.CharField(max_length=10, choices=Interval.choices, blank=True)
    news_source = models.ForeignKey(
        "news_data.NewsSource",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="collection_runs",
    )
    news_feed = models.ForeignKey(
        "news_data.NewsFeed",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="collection_runs",
    )
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
    failed_count = models.PositiveIntegerField(default=0)
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
        if self.news_feed_id:
            return (
                f"{self.news_feed.code}:{self.status}:"
                f"{self.started_at.isoformat()}"
            )
        if self.news_source_id:
            return (
                f"{self.news_source.code}:{self.status}:"
                f"{self.started_at.isoformat()}"
            )
        return f"{self.symbol}:{self.interval}:{self.status}:{self.started_at.isoformat()}"
