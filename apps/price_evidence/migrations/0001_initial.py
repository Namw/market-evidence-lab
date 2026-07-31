import apps.price_evidence.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("research_cases", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("calculation_version", models.CharField(default="v1", editable=False, max_length=20)),
                ("range_start", models.DateTimeField()),
                ("range_end", models.DateTimeField()),
                ("quality_status", models.CharField(choices=[("complete", "完整"), ("partial", "部分缺失"), ("inconsistent", "不一致"), ("unavailable", "不可用")], max_length=20)),
                ("expected_count", models.PositiveSmallIntegerField(default=24)),
                ("actual_count", models.PositiveSmallIntegerField(default=0)),
                ("missing_open_times", models.JSONField(default=apps.price_evidence.models.empty_list)),
                ("hourly_klines_snapshot", models.JSONField(default=apps.price_evidence.models.empty_list)),
                ("metrics_snapshot", models.JSONField(default=apps.price_evidence.models.empty_dict)),
                ("daily_consistency_snapshot", models.JSONField(default=apps.price_evidence.models.empty_dict)),
                ("generated_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("research_case", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="price_evidence", to="research_cases.researchcase")),
            ],
            options={"ordering": ["-generated_at"]},
        ),
    ]
