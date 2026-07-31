from django.db import models


def empty_dict():
    return {}


class DerivativesEvidence(models.Model):
    class Status(models.TextChoices):
        COMPLETE = "complete", "完整"
        PARTIAL = "partial", "部分可用"
        UNAVAILABLE = "unavailable", "来源不可用"
        FAILED = "failed", "计算失败"

    research_case = models.OneToOneField(
        "research_cases.ResearchCase",
        on_delete=models.CASCADE,
        related_name="derivatives_evidence",
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    rule_version = models.CharField(
        max_length=40,
        default="derivatives-evidence-v1",
        editable=False,
    )
    calculated_at = models.DateTimeField()
    data_range_start = models.DateTimeField()
    data_range_end = models.DateTimeField()
    coverage_snapshot = models.JSONField(default=empty_dict)
    calculation_snapshot = models.JSONField(default=empty_dict)
    rule_snapshot = models.JSONField(default=empty_dict)
    source_snapshot = models.JSONField(default=empty_dict)
    status_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-calculated_at"]

    def __str__(self) -> str:
        return f"{self.research_case}:derivatives:{self.status}"
