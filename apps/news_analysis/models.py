from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models


class NewsAnalysisRun(models.Model):
    class Trigger(models.TextChoices):
        MANUAL = "manual", "页面手动"
        SCHEDULED = "scheduled", "定时工作流"
        COMMAND = "command", "命令行"

    class Mode(models.TextChoices):
        INCREMENTAL = "incremental", "增量分析"
        RETRY_FAILED = "retry_failed", "重试失败"
        SMOKE = "smoke", "单条冒烟"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"
        NOT_RUN = "not_run", "未执行"

    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    analysis_version = models.CharField(max_length=80)
    prompt_version = models.CharField(max_length=80)
    model_name = models.CharField(max_length=160)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    candidate_count = models.PositiveIntegerField(default=0)
    rule_processed_count = models.PositiveIntegerField(default=0)
    ai_processed_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    api_request_count = models.PositiveIntegerField(default=0)
    retry_count = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis_version"],
                condition=models.Q(status="running"),
                name="news_analysis_one_running_ver",
            )
        ]
        indexes = [
            models.Index(
                fields=["analysis_version", "-started_at"],
                name="news_an_run_ver_start_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.analysis_version}:{self.mode}:{self.status}"


class NewsAnalysisResult(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    class ObservationResult(models.TextChoices):
        NOTEWORTHY = "noteworthy", "值得关注"
        ROUTINE = "routine", "常规信息"
        NOISE = "noise", "明显噪声"
        INSUFFICIENT = "insufficient", "信息不足"

    class EventType(models.TextChoices):
        PROTOCOL_UPGRADE = "protocol_upgrade", "协议升级"
        SECURITY_INCIDENT = "security_incident", "安全事件"
        REGULATION_POLICY = "regulation_policy", "监管政策"
        INSTITUTIONAL_ADOPTION = "institutional_adoption", "机构采用"
        ECOSYSTEM_DEVELOPMENT = "ecosystem_development", "生态发展"
        LISTING_DELISTING = "listing_delisting", "上币或下币"
        TRADING_RULE_CHANGE = "trading_rule_change", "交易规则变化"
        PLATFORM_OPERATION = "platform_operation", "平台运营"
        MARKET_ACTIVITY = "market_activity", "市场活动"
        MARKETING_ACTIVITY = "marketing_activity", "营销活动"
        RESEARCH_REPORT = "research_report", "研究报告"
        OTHER = "other", "其他"
        UNCLEAR = "unclear", "无法判断"

    class ImpactScope(models.TextChoices):
        ETHEREUM = "ethereum", "以太坊"
        ETHEREUM_ECOSYSTEM = "ethereum_ecosystem", "以太坊生态"
        CRYPTO_MARKET = "crypto_market", "加密市场"
        EXCHANGE = "exchange", "交易所"
        OTHER_ASSET = "other_asset", "其他资产"
        UNCLEAR = "unclear", "无法判断"

    class Level(models.TextChoices):
        HIGH = "high", "高"
        MEDIUM = "medium", "中"
        LOW = "low", "低"

    class Method(models.TextChoices):
        RULE = "rule", "固定规则"
        AI = "ai", "AI"

    news_record = models.ForeignKey(
        "news_data.NewsRawRecord",
        on_delete=models.PROTECT,
        related_name="analysis_results",
    )
    analysis_version = models.CharField(max_length=80)
    prompt_version = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=Status.choices)
    observation_result = models.CharField(
        max_length=20, choices=ObservationResult.choices, blank=True
    )
    event_type = models.CharField(
        max_length=40, choices=EventType.choices, blank=True
    )
    impact_scope = models.CharField(
        max_length=40, choices=ImpactScope.choices, blank=True
    )
    importance = models.CharField(max_length=20, choices=Level.choices, blank=True)
    rationale = models.CharField(max_length=500, blank=True)
    confidence = models.CharField(max_length=20, choices=Level.choices, blank=True)
    method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    matched_rule_id = models.CharField(max_length=100, blank=True)
    actual_model_name = models.CharField(max_length=160, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    analysis_run = models.ForeignKey(
        NewsAnalysisRun,
        on_delete=models.PROTECT,
        related_name="results",
    )
    analyzed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-analyzed_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["news_record", "analysis_version"],
                name="news_analysis_record_ver_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["analysis_version", "status"],
                name="news_an_res_ver_stat_idx",
            ),
            models.Index(
                fields=["analysis_version", "observation_result"],
                name="news_an_res_ver_obs_idx",
            ),
        ]

    def clean(self):
        super().clean()
        classified_fields = (
            self.observation_result,
            self.event_type,
            self.impact_scope,
            self.importance,
            self.rationale.strip(),
            self.confidence,
            self.method,
        )
        if self.status == self.Status.SUCCESS and not all(classified_fields):
            raise ValidationError("成功结果必须包含完整的结构化判断字段。")
        if self.status == self.Status.FAILED and any(classified_fields):
            raise ValidationError("失败结果不能伪造结构化分类。")

    def __str__(self) -> str:
        return f"{self.news_record_id}:{self.analysis_version}:{self.status}"
