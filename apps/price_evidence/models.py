from django.db import models


def empty_list():
    return []


def empty_dict():
    return {}


class PriceEvidence(models.Model):
    class QualityStatus(models.TextChoices):
        COMPLETE = "complete", "完整"
        PARTIAL = "partial", "部分缺失"
        INCONSISTENT = "inconsistent", "不一致"
        UNAVAILABLE = "unavailable", "不可用"

    research_case = models.OneToOneField(
        "research_cases.ResearchCase",
        on_delete=models.CASCADE,
        related_name="price_evidence",
    )
    calculation_version = models.CharField(max_length=20, default="v1", editable=False)
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    quality_status = models.CharField(max_length=20, choices=QualityStatus.choices)
    expected_count = models.PositiveSmallIntegerField(default=24)
    actual_count = models.PositiveSmallIntegerField(default=0)
    missing_open_times = models.JSONField(default=empty_list)
    hourly_klines_snapshot = models.JSONField(default=empty_list)
    metrics_snapshot = models.JSONField(default=empty_dict)
    daily_consistency_snapshot = models.JSONField(default=empty_dict)
    generated_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return f"{self.research_case}:price:{self.quality_status}"
