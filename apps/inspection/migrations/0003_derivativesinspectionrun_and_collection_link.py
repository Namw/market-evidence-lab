import apps.inspection.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0003_collectionrun_failed_count_and_more"),
        ("inspection", "0002_klineinspectionrun_scheduled_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="klineinspectionrun",
            name="source_collection_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="kline_inspections",
                to="collection.collectionrun",
            ),
        ),
        migrations.CreateModel(
            name="DerivativesInspectionRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "data_type",
                    models.CharField(
                        choices=[("open_interest", "OI"), ("funding", "Funding")],
                        max_length=20,
                    ),
                ),
                (
                    "exchange",
                    models.CharField(choices=[("binance", "Binance")], max_length=20),
                ),
                (
                    "market_type",
                    models.CharField(
                        choices=[("usd_m_futures", "USD-M Futures")],
                        max_length=30,
                    ),
                ),
                ("symbol", models.CharField(max_length=20)),
                ("range_start", models.DateTimeField()),
                ("range_end", models.DateTimeField()),
                (
                    "trigger",
                    models.CharField(
                        choices=[("scheduled", "定时"), ("manual", "手工")],
                        default="manual",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "运行中"),
                            ("success", "成功"),
                            ("failed", "失败"),
                        ],
                        default="running",
                        max_length=20,
                    ),
                ),
                (
                    "quality_status",
                    models.CharField(
                        choices=[
                            ("pending", "待判定"),
                            ("passed", "通过"),
                            ("issues", "发现问题"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("expected_count", models.PositiveIntegerField(default=0)),
                ("actual_count", models.PositiveIntegerField(default=0)),
                ("issue_count", models.PositiveIntegerField(default=0)),
                ("empty_count", models.PositiveIntegerField(default=0)),
                ("missing_count", models.PositiveIntegerField(default=0)),
                ("duplicate_count", models.PositiveIntegerField(default=0)),
                ("sequence_issue_count", models.PositiveIntegerField(default=0)),
                ("misaligned_count", models.PositiveIntegerField(default=0)),
                ("invalid_numeric_count", models.PositiveIntegerField(default=0)),
                (
                    "details",
                    models.JSONField(
                        default=apps.inspection.models.empty_derivatives_inspection_details
                    ),
                ),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "source_collection_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="derivatives_inspections",
                        to="collection.collectionrun",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
                "indexes": [
                    models.Index(
                        fields=["-started_at"],
                        name="deriv_inspect_start_idx",
                    ),
                    models.Index(
                        fields=["symbol", "data_type", "-started_at"],
                        name="deriv_ins_sym_type_idx",
                    ),
                ],
            },
        ),
    ]
