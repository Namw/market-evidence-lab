from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .objective_fact_schema import EVENT_STATUS_CHOICES


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

    class Conclusion(models.TextChoices):
        BULLISH = "bullish", "利好"
        BEARISH = "bearish", "利空"
        UNCLEAR = "unclear", "模糊不清"
        IRRELEVANT = "irrelevant", "无关"

    class ClassificationStage(models.TextChoices):
        TITLE_RULE = "title_rule", "程序判断标题"
        TITLE_AI = "title_ai", "AI 判断标题"
        SUMMARY_AI = "summary_ai", "AI 判断 RSS 摘要"
        CONTENT_AI = "content_ai", "AI 判断正文"

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
    conclusion = models.CharField(
        max_length=20, choices=Conclusion.choices, blank=True
    )
    classification_stage = models.CharField(
        max_length=20, choices=ClassificationStage.choices, blank=True
    )
    rationale = models.CharField(max_length=500, blank=True)
    content_summary = models.TextField(blank=True)
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
                fields=["analysis_version", "conclusion"],
                name="news_an_res_ver_concl_idx",
            ),
        ]

    def clean(self):
        super().clean()
        classified_fields = (
            self.conclusion,
            self.classification_stage,
            self.rationale.strip(),
            self.method,
        )
        if self.status == self.Status.SUCCESS and not all(classified_fields):
            raise ValidationError("成功结果必须包含完整的结构化判断字段。")
        if self.status == self.Status.FAILED and any(classified_fields):
            raise ValidationError("失败结果不能伪造结构化分类。")

    def __str__(self) -> str:
        return f"{self.news_record_id}:{self.analysis_version}:{self.status}"


class ObjectiveFactExtractionRun(models.Model):
    class Trigger(models.TextChoices):
        MANUAL = "manual", "页面手动"
        COMMAND = "command", "命令行"

    class Mode(models.TextChoices):
        INCREMENTAL = "incremental", "增量提取"
        RETRY_FAILED = "retry_failed", "重试失败"
        SINGLE = "single", "单条提取"
        RETRY_SINGLE = "retry_single", "单条重试"
        REEXTRACT = "reextract", "重新提取"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        PARTIAL = "partial", "部分成功"
        FAILED = "failed", "失败"
        NOT_RUN = "not_run", "未执行"

    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    triggered_by = models.CharField(max_length=150, blank=True)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    provider = models.CharField(max_length=80)
    model = models.CharField(max_length=160)
    prompt_version = models.CharField(max_length=80)
    generation_parameters = models.JSONField(default=dict)
    concurrency_slot = models.CharField(max_length=40, default="objective_fact")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    candidate_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    request_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    validation_passed_count = models.PositiveIntegerField(default=0)
    validation_warning_count = models.PositiveIntegerField(default=0)
    validation_error_count = models.PositiveIntegerField(default=0)
    facts_count = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["concurrency_slot"],
                condition=models.Q(status="running"),
                name="objective_fact_one_running_global",
            )
        ]
        indexes = [
            models.Index(
                fields=["prompt_version", "-started_at"],
                name="obj_fact_run_prompt_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.prompt_version}:{self.mode}:{self.status}"


class ObjectiveFactExtractionResult(models.Model):
    class ExtractionStatus(models.TextChoices):
        PENDING = "pending", "提取中"
        SUCCESS = "success", "提取成功"
        FAILED = "failed", "提取失败"

    class ValidationStatus(models.TextChoices):
        PASSED = "passed", "校验通过"
        WARNING = "warning", "校验警告"
        ERROR = "error", "校验错误"

    news_record = models.ForeignKey(
        "news_data.NewsRawRecord",
        on_delete=models.PROTECT,
        related_name="objective_fact_results",
    )
    extraction_run = models.ForeignKey(
        ObjectiveFactExtractionRun,
        on_delete=models.PROTECT,
        related_name="results",
    )
    extraction_status = models.CharField(
        max_length=20,
        choices=ExtractionStatus.choices,
        default=ExtractionStatus.PENDING,
    )
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.ERROR,
    )
    ai_call_succeeded = models.BooleanField(default=False)
    json_parse_succeeded = models.BooleanField(default=False)
    provider = models.CharField(max_length=80)
    model = models.CharField(max_length=160)
    prompt_version = models.CharField(max_length=80)
    generation_parameters = models.JSONField(default=dict)
    request_count = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    input_snapshot = models.JSONField(default=dict)
    has_stored_body = models.BooleanField(default=False)
    stored_body_included = models.BooleanField(default=False)
    input_scope_note = models.CharField(max_length=300, blank=True)
    system_prompt = models.TextField()
    user_prompt = models.TextField()
    api_response = models.JSONField(null=True, blank=True)
    raw_model_output = models.TextField(blank=True)
    parsed_result = models.JSONField(null=True, blank=True)
    json_parse_error = models.TextField(blank=True)
    validation_errors = models.JSONField(default=list)
    validation_warnings = models.JSONField(default=list)
    evidence_matches = models.JSONField(default=list)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    objective_summary = models.TextField(blank=True)
    event_status = models.CharField(
        max_length=40, choices=EVENT_STATUS_CHOICES, blank=True
    )
    information_completeness = models.CharField(max_length=20, blank=True)
    facts_count = models.PositiveIntegerField(default=0)
    extracted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-extracted_at", "-id"]
        indexes = [
            models.Index(
                fields=["news_record", "prompt_version", "-id"],
                name="obj_fact_news_prompt_idx",
            ),
            models.Index(
                fields=["prompt_version", "extraction_status"],
                name="obj_fact_prompt_extract_idx",
            ),
            models.Index(
                fields=["prompt_version", "validation_status"],
                name="obj_fact_prompt_valid_idx",
            ),
            models.Index(
                fields=["event_status", "information_completeness"],
                name="obj_fact_event_info_idx",
            ),
            models.Index(fields=["facts_count"], name="obj_fact_count_idx"),
            models.Index(fields=["has_stored_body"], name="obj_fact_body_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.news_record_id}:{self.prompt_version}:{self.extraction_status}"

    @property
    def is_evidence_chain_eligible(self) -> bool:
        """Return whether this complete extraction can enter a downstream evidence chain."""
        return self.facts_count > 0 and self.validation_errors == []


class EventMergeRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "等待中"
        RUNNING = "running", "运行中"
        SUCCEEDED = "succeeded", "成功"
        SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings", "成功（有警告）"
        FAILED = "failed", "失败"
        CANCELLED = "cancelled", "已取消"

    class Trigger(models.TextChoices):
        MANUAL = "manual", "页面手动"
        SCHEDULED = "scheduled", "定时工作流"
        RETRY_FAILED = "retry_failed", "重试失败项"
        FULL_REBUILD = "full_rebuild", "完整重建"

    class Stage(models.TextChoices):
        PENDING = "pending", "等待开始"
        SCANNING = "scanning", "扫描输入"
        CANDIDATES = "candidates", "生成候选"
        HARD_RULES = "hard_rules", "执行硬规则"
        AI_DECISIONS = "ai_decisions", "AI 判断"
        GROUPING = "grouping", "事件归组"
        VALIDATING = "validating", "一致性校验"
        ACTIVATING = "activating", "切换有效快照"
        COMPLETED = "completed", "完成"

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    original_run = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="retry_runs"
    )
    retry_pair_decision = models.ForeignKey(
        "EventPairDecision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="single_retry_runs",
    )
    request_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    current_stage = models.CharField(max_length=30, choices=Stage.choices, default=Stage.PENDING)
    total_progress = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    input_count = models.PositiveIntegerField(default=0)
    eligible_count = models.PositiveIntegerField(default=0)
    candidate_pair_count = models.PositiveIntegerField(default=0)
    hard_rejected_count = models.PositiveIntegerField(default=0)
    ai_decision_count = models.PositiveIntegerField(default=0)
    ai_failure_count = models.PositiveIntegerField(default=0)
    auto_grouped_event_count = models.PositiveIntegerField(default=0)
    singleton_event_count = models.PositiveIntegerField(default=0)
    uncertain_event_count = models.PositiveIntegerField(default=0)
    final_event_count = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    algorithm_version = models.CharField(max_length=80)
    prompt_version = models.CharField(max_length=80)
    model = models.CharField(max_length=160)
    configuration_snapshot = models.JSONField(default=dict)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    is_current_snapshot = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="running"),
                name="event_merge_one_running_global",
            ),
            models.UniqueConstraint(
                fields=["is_current_snapshot"],
                condition=models.Q(is_current_snapshot=True),
                name="event_merge_one_current_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["-started_at"], name="event_merge_started_idx"),
            models.Index(
                fields=["algorithm_version", "prompt_version"],
                name="event_merge_versions_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"event-merge:{self.pk}:{self.status}"

    @property
    def duration_seconds(self) -> int | None:
        if self.started_at is None:
            return None
        end = self.finished_at or timezone.now()
        return max(0, int((end - self.started_at).total_seconds()))


class CanonicalEvent(models.Model):
    class Status(models.TextChoices):
        PROVISIONAL = "provisional", "暂定"
        UNCERTAIN = "uncertain", "不确定"
        CONFLICTED = "conflicted", "存在冲突"

    class GroupingMethod(models.TextChoices):
        SINGLETON = "singleton", "单成员"
        AUTO_GROUPED = "auto_grouped", "自动归组"

    run = models.ForeignKey(EventMergeRun, on_delete=models.PROTECT, related_name="events")
    canonical_title = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROVISIONAL)
    grouping_method = models.CharField(max_length=20, choices=GroupingMethod.choices)
    actors_snapshot = models.JSONField(default=list)
    action_snapshot = models.TextField(blank=True)
    object_snapshot = models.JSONField(default=list)
    event_status_snapshot = models.CharField(max_length=40, blank=True)
    objective_summary = models.TextField(blank=True)
    event_time_text = models.CharField(max_length=80, blank=True)
    earliest_publication_at = models.DateTimeField()
    latest_publication_at = models.DateTimeField()
    member_count = models.PositiveIntegerField(default=0)
    source_count = models.PositiveIntegerField(default=0)
    grouping_confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-latest_publication_at", "id"]
        indexes = [
            models.Index(fields=["run", "status"], name="canon_event_run_status_idx"),
            models.Index(fields=["run", "grouping_method"], name="canon_event_run_group_idx"),
        ]

    def __str__(self) -> str:
        return self.canonical_title


class EventMembership(models.Model):
    class Role(models.TextChoices):
        PRIMARY = "primary", "主要成员"
        CORROBORATING = "corroborating", "补充成员"

    class JoinMethod(models.TextChoices):
        SINGLETON = "singleton", "单成员创建"
        AUTO_MATCH = "auto_match", "高置信自动匹配"

    event = models.ForeignKey(CanonicalEvent, on_delete=models.CASCADE, related_name="memberships")
    extraction_result = models.ForeignKey(
        ObjectiveFactExtractionResult, on_delete=models.PROTECT, related_name="event_memberships"
    )
    news_record = models.ForeignKey(
        "news_data.NewsRawRecord", on_delete=models.PROTECT, related_name="event_memberships"
    )
    member_role = models.CharField(max_length=20, choices=Role.choices)
    join_method = models.CharField(max_length=20, choices=JoinMethod.choices)
    match_confidence = models.FloatField(null=True, blank=True)
    match_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["event_id", "news_record__published_at", "extraction_result_id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "extraction_result"], name="event_member_unique_result"),
            models.UniqueConstraint(fields=["event", "news_record"], name="event_member_unique_news"),
        ]
        indexes = [
            models.Index(fields=["extraction_result"], name="event_member_result_idx"),
            models.Index(fields=["news_record"], name="event_member_news_idx"),
        ]


class EventPairDecision(models.Model):
    class Relation(models.TextChoices):
        SAME_EVENT = "same_event", "同一事件"
        NOT_SAME_EVENT = "not_same_event", "不同事件"
        UNCERTAIN = "uncertain", "不确定"
        HARD_REJECTED = "hard_rejected", "硬规则拒绝"
        PROCESSING_FAILED = "processing_failed", "处理失败"

    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "待处理"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "失败"

    run = models.ForeignKey(EventMergeRun, on_delete=models.CASCADE, related_name="pair_decisions")
    left_result = models.ForeignKey(
        ObjectiveFactExtractionResult, on_delete=models.PROTECT, related_name="event_pair_decisions_left"
    )
    right_result = models.ForeignKey(
        ObjectiveFactExtractionResult, on_delete=models.PROTECT, related_name="event_pair_decisions_right"
    )
    relation = models.CharField(max_length=30, choices=Relation.choices)
    confidence = models.FloatField(null=True, blank=True)
    same_event_basis = models.JSONField(default=list)
    differences = models.JSONField(default=list)
    reason = models.TextField(blank=True)
    canonical_title = models.TextField(blank=True)
    has_fact_conflict = models.BooleanField(default=False)
    algorithm_version = models.CharField(max_length=80)
    prompt_version = models.CharField(max_length=80)
    model = models.CharField(max_length=160)
    structured_response = models.JSONField(null=True, blank=True)
    processing_status = models.CharField(max_length=20, choices=ProcessingStatus.choices)
    attempt_count = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    is_retryable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["left_result_id", "right_result_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "left_result", "right_result"], name="event_pair_unique_per_run"
            ),
            models.CheckConstraint(
                condition=models.Q(left_result_id__lt=models.F("right_result_id")),
                name="event_pair_canonical_order",
            ),
        ]
        indexes = [
            models.Index(fields=["run", "relation"], name="event_pair_run_relation_idx"),
            models.Index(fields=["run", "processing_status"], name="event_pair_run_process_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.left_result_id}-{self.right_result_id}:{self.relation}"
