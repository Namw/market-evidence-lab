from django.db import models


def empty_inspection_details():
    return {
        "missing_ranges": [],
        "duplicate_open_times": [],
        "misaligned_open_times": [],
        "invalid_rows": [],
        "details_truncated": False,
    }


def empty_derivatives_inspection_details():
    return {
        "no_data": False,
        "missing_ranges": [],
        "missing_settlements": [],
        "duplicate_timestamps": [],
        "sequence_issues": [],
        "misaligned_timestamps": [],
        "invalid_rows": [],
        "details_truncated": False,
    }


def empty_news_dimensions():
    return {
        "availability": False,
        "parsing": False,
        "coverage": False,
        "key_fields": False,
        "timeliness": False,
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
        SCHEDULED = "scheduled", "定时"
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
    source_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kline_inspections",
    )

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

    @property
    def other_issue_count(self) -> int:
        return sum(
            (
                self.duplicate_count,
                self.misaligned_count,
                self.invalid_ohlc_count,
                self.invalid_numeric_count,
                self.invalid_close_time_count,
            )
        )


class DerivativesInspectionRun(models.Model):
    class DataType(models.TextChoices):
        OPEN_INTEREST = "open_interest", "OI"
        FUNDING = "funding", "Funding"

    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时"
        MANUAL = "manual", "手工"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "待判定"
        PASSED = "passed", "通过"
        ISSUES = "issues", "发现问题"

    data_type = models.CharField(max_length=20, choices=DataType.choices)
    exchange = models.CharField(max_length=20, choices=Exchange.choices)
    market_type = models.CharField(max_length=30, choices=MarketType.choices)
    symbol = models.CharField(max_length=20)
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
    issue_count = models.PositiveIntegerField(default=0)
    empty_count = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    sequence_issue_count = models.PositiveIntegerField(default=0)
    misaligned_count = models.PositiveIntegerField(default=0)
    invalid_numeric_count = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=empty_derivatives_inspection_details)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derivatives_inspections",
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"], name="deriv_inspect_start_idx"),
            models.Index(
                fields=["symbol", "data_type", "-started_at"],
                name="deriv_ins_sym_type_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.symbol}:{self.data_type}:{self.status}:"
            f"{self.quality_status}:{self.started_at.isoformat()}"
        )

    @property
    def other_issue_count(self) -> int:
        return max(self.issue_count - self.missing_count, 0)


class NewsInspectionRun(models.Model):
    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时"
        MANUAL = "manual", "手工"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "完成"
        FAILED = "failed", "检查执行失败"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "待判定"
        PASSED = "passed", "通过"
        WARNING = "warning", "警告"
        FAILED = "failed", "失败"

    source = models.ForeignKey(
        "news_data.NewsSource",
        on_delete=models.PROTECT,
        related_name="inspection_runs",
    )
    feed = models.ForeignKey(
        "news_data.NewsFeed",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inspection_runs",
    )
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    trigger = models.CharField(
        max_length=20, choices=Trigger.choices, default=Trigger.MANUAL
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    quality_status = models.CharField(
        max_length=20,
        choices=QualityStatus.choices,
        default=QualityStatus.PENDING,
    )
    coverage_complete = models.BooleanField(default=False)
    candidate_count = models.PositiveIntegerField(default=0)
    parsed_count = models.PositiveIntegerField(default=0)
    eligible_count = models.PositiveIntegerField(default=0)
    inserted_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    dimensions = models.JSONField(default=empty_news_dimensions)
    reasons = models.JSONField(default=list)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news_inspections",
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"], name="news_insp_started_idx"),
            models.Index(
                fields=["source", "-started_at"], name="news_insp_src_start_idx"
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.source.code}:{self.quality_status}:"
            f"{self.started_at.isoformat()}"
        )
