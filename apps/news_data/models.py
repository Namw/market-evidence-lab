from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone


class NewsSource(models.Model):
    class SourceType(models.TextChoices):
        OFFICIAL = "official", "官方"

    class CollectionMethod(models.TextChoices):
        RSS = "rss", "RSS / Atom"
        WEB = "web", "公开结构化列表"

    class ObservationScope(models.TextChoices):
        ETH_DIRECT = "eth_direct", "ETH 直接事实"
        CRYPTO_SYSTEMIC = "crypto_systemic", "加密市场系统性"

    class AuthorityLevel(models.TextChoices):
        HIGHEST = "highest", "最高"
        MEDIUM = "medium", "中等"
        GENERAL = "general", "一般"

    class InspectionStatus(models.TextChoices):
        NEVER_RUN = "never_run", "从未运行"
        PASSED = "passed", "通过"
        WARNING = "warning", "警告"
        FAILED = "failed", "失败"

    class HealthStatus(models.TextChoices):
        NEVER_RUN = "never_run", "从未运行"
        HEALTHY = "healthy", "健康"
        DEGRADED = "degraded", "降级"
        BROKEN = "broken", "故障"

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    enabled = models.BooleanField(default=True)
    activated_at = models.DateTimeField()
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    collection_method = models.CharField(
        max_length=20, choices=CollectionMethod.choices
    )
    observation_scope = models.CharField(
        max_length=30, choices=ObservationScope.choices
    )
    authority_level = models.CharField(
        max_length=20,
        choices=AuthorityLevel.choices,
        default=AuthorityLevel.GENERAL,
    )
    base_url = models.URLField(max_length=500)
    feed_url = models.URLField(max_length=500, blank=True)
    parser_version = models.CharField(max_length=80)
    last_run_at = models.DateTimeField(null=True, blank=True)
    trusted_coverage_end = models.DateTimeField(null=True, blank=True)
    last_inspection_status = models.CharField(
        max_length=20,
        choices=InspectionStatus.choices,
        default=InspectionStatus.NEVER_RUN,
    )
    health_status = models.CharField(
        max_length=20,
        choices=HealthStatus.choices,
        default=HealthStatus.NEVER_RUN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name

    def health_at(self, now=None) -> str:
        now = now or timezone.now()
        if self.last_run_at is None:
            return self.HealthStatus.NEVER_RUN
        if self.last_inspection_status == self.InspectionStatus.FAILED:
            return self.HealthStatus.BROKEN
        reference = self.trusted_coverage_end or self.last_run_at
        age = now - reference
        if age > timedelta(hours=72):
            return self.HealthStatus.BROKEN
        if (
            age > timedelta(hours=36)
            or self.last_inspection_status == self.InspectionStatus.WARNING
        ):
            return self.HealthStatus.DEGRADED
        return self.HealthStatus.HEALTHY

    @property
    def current_health_status(self) -> str:
        return self.health_at()

    @property
    def current_health_status_display(self) -> str:
        return dict(self.HealthStatus.choices)[self.current_health_status]


class NewsFeed(models.Model):
    source = models.ForeignKey(
        NewsSource, on_delete=models.PROTECT, related_name="feeds"
    )
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=160)
    enabled = models.BooleanField(default=True)
    activated_at = models.DateTimeField()
    feed_url = models.URLField(max_length=500)
    parser_version = models.CharField(max_length=80)
    bootstrap_visible_items = models.BooleanField(default=False)
    last_run_at = models.DateTimeField(null=True, blank=True)
    trusted_coverage_end = models.DateTimeField(null=True, blank=True)
    last_inspection_status = models.CharField(
        max_length=20,
        choices=NewsSource.InspectionStatus.choices,
        default=NewsSource.InspectionStatus.NEVER_RUN,
    )
    health_status = models.CharField(
        max_length=20,
        choices=NewsSource.HealthStatus.choices,
        default=NewsSource.HealthStatus.NEVER_RUN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source__code", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "name"], name="news_feed_source_name_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.source.name} · {self.name}"

    def health_at(self, now=None) -> str:
        now = now or timezone.now()
        if self.last_run_at is None:
            return NewsSource.HealthStatus.NEVER_RUN
        if self.last_inspection_status == NewsSource.InspectionStatus.FAILED:
            return NewsSource.HealthStatus.BROKEN
        reference = self.trusted_coverage_end or self.last_run_at
        age = now - reference
        if age > timedelta(hours=72):
            return NewsSource.HealthStatus.BROKEN
        if (
            age > timedelta(hours=36)
            or self.last_inspection_status == NewsSource.InspectionStatus.WARNING
        ):
            return NewsSource.HealthStatus.DEGRADED
        return NewsSource.HealthStatus.HEALTHY

    @property
    def current_health_status(self) -> str:
        return self.health_at()

    @property
    def current_health_status_display(self) -> str:
        return dict(NewsSource.HealthStatus.choices)[self.current_health_status]


class NewsRawRecord(models.Model):
    source = models.ForeignKey(
        NewsSource, on_delete=models.PROTECT, related_name="raw_records"
    )
    source_item_id = models.CharField(max_length=255, blank=True)
    original_url = models.URLField(max_length=1000, blank=True)
    canonical_url = models.URLField(max_length=1000, blank=True)
    title = models.TextField()
    summary = models.TextField(blank=True)
    published_at = models.DateTimeField()
    updated_at_source = models.DateTimeField(null=True, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    language = models.CharField(max_length=20, default="en")
    source_category = models.CharField(max_length=255, blank=True)
    source_tags = models.JSONField(default=list)
    source_author = models.TextField(blank=True)
    feeds = models.ManyToManyField(
        NewsFeed, through="NewsRecordFeed", related_name="raw_records"
    )
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    identity_hash = models.CharField(max_length=64)
    content_hash = models.CharField(max_length=64)
    raw_payload = models.JSONField(default=dict)
    first_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.PROTECT,
        related_name="first_seen_news_records",
    )
    last_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_seen_news_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_item_id"],
                condition=~models.Q(source_item_id=""),
                name="news_source_item_unique",
            ),
            models.UniqueConstraint(
                fields=["source", "canonical_url"],
                condition=~models.Q(canonical_url=""),
                name="news_source_url_unique",
            ),
            models.UniqueConstraint(
                fields=["source", "identity_hash"],
                name="news_source_identity_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source", "-published_at"], name="news_src_pub_desc_idx"
            ),
            models.Index(fields=["identity_hash"], name="news_identity_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source.code}:{self.title[:80]}"


class NewsRecordFeed(models.Model):
    news_record = models.ForeignKey(
        NewsRawRecord, on_delete=models.CASCADE, related_name="feed_memberships"
    )
    feed = models.ForeignKey(
        NewsFeed, on_delete=models.PROTECT, related_name="record_memberships"
    )
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    first_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.PROTECT,
        related_name="first_seen_news_feed_memberships",
    )
    last_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_seen_news_feed_memberships",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["news_record", "feed"], name="news_record_feed_unique"
            )
        ]


class NewsCollectionDiagnostic(models.Model):
    class UnitType(models.TextChoices):
        FEED = "feed", "Feed"
        PAGE = "page", "分页"

    class StopReason(models.TextChoices):
        REACHED_TIME_BOUNDARY = "reached_time_boundary", "到达时间边界"
        NO_NEXT_PAGE = "no_next_page", "没有下一页"
        SOURCE_HISTORY_LIMITED = "source_history_limited", "来源历史有限"
        SAFETY_PAGE_LIMIT = "safety_page_limit", "安全页数上限"
        PAGINATION_LOOP = "pagination_loop", "分页循环"
        REQUEST_FAILED = "request_failed", "请求失败"
        COMPLETED = "completed", "完成"

    collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.CASCADE,
        related_name="news_diagnostics",
    )
    source = models.ForeignKey(
        NewsSource, on_delete=models.PROTECT, related_name="diagnostics"
    )
    feed = models.ForeignKey(
        NewsFeed,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="diagnostics",
    )
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    unit_identifier = models.CharField(max_length=255)
    request_started_at = models.DateTimeField()
    request_finished_at = models.DateTimeField()
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    final_url = models.URLField(max_length=1000, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    response_size = models.PositiveIntegerField(default=0)
    response_hash = models.CharField(max_length=64, blank=True)
    parser_version = models.CharField(max_length=80)
    request_count = models.PositiveIntegerField(default=0)
    retry_count = models.PositiveIntegerField(default=0)
    candidate_count = models.PositiveIntegerField(default=0)
    parsed_count = models.PositiveIntegerField(default=0)
    eligible_count = models.PositiveIntegerField(default=0)
    inserted_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    earliest_published_at = models.DateTimeField(null=True, blank=True)
    latest_published_at = models.DateTimeField(null=True, blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    stop_reason = models.CharField(
        max_length=40, choices=StopReason.choices, blank=True
    )
    coverage_complete = models.BooleanField(default=False)
    error_code = models.CharField(max_length=80, blank=True)
    error_summary = models.TextField(blank=True)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-request_started_at", "-id"]
        indexes = [
            models.Index(
                fields=["source", "-request_started_at"],
                name="news_diag_src_req_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source.code}:{self.unit_identifier}"
