import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def populate_report_symbols(apps, schema_editor):
    Report = apps.get_model("microstructure", "MarketPilotReport")
    for report in Report.objects.select_related("run").all().iterator():
        report.symbol = report.run.symbol
        report.save(update_fields=["symbol"])


class Migration(migrations.Migration):
    dependencies = [("microstructure", "0008_market_pilot_reports")]

    operations = [
        migrations.AddField(
            model_name="marketpilotrun",
            name="mode",
            field=models.CharField(
                choices=[
                    ("historical", "历史预演"),
                    ("live", "实时影子监控"),
                ],
                default="historical",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="marketpilotrun",
            name="trigger",
            field=models.CharField(
                choices=[("manual", "手工"), ("scheduled", "定时")],
                default="manual",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="symbol",
            field=models.CharField(max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="status",
            field=models.CharField(
                choices=[
                    ("awaiting_outcomes", "等待验证"),
                    ("completed", "已完成"),
                    ("failed", "失败"),
                ],
                default="completed",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="notification_status",
            field=models.CharField(
                choices=[
                    ("pending", "待推送"),
                    ("sent", "已推送"),
                    ("failed", "推送失败"),
                    ("not_configured", "未配置"),
                ],
                default="not_configured",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="notification_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="notification_error",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketpilotreport",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.RemoveConstraint(
            model_name="marketpilotreport",
            name="pilot_report_run_window_unique",
        ),
        migrations.RunPython(populate_report_symbols, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="marketpilotreport",
            name="symbol",
            field=models.CharField(max_length=20),
        ),
        migrations.AddConstraint(
            model_name="marketpilotreport",
            constraint=models.UniqueConstraint(
                fields=("symbol", "window_start"),
                name="pilot_report_symbol_window_unique",
            ),
        ),
        migrations.CreateModel(
            name="MarketPilotWindowCheck",
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
                ("symbol", models.CharField(max_length=20)),
                ("window_start", models.DateTimeField()),
                ("window_end", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("waiting_data", "等待数据"),
                            ("normal", "未达异常"),
                            ("analyzing", "AI 分析中"),
                            ("analyzed", "已生成报告"),
                            ("failed", "失败"),
                        ],
                        default="waiting_data",
                        max_length=20,
                    ),
                ),
                (
                    "return_pct",
                    models.DecimalField(
                        blank=True, decimal_places=6, max_digits=12, null=True
                    ),
                ),
                (
                    "threshold_pct",
                    models.DecimalField(decimal_places=3, max_digits=6),
                ),
                ("data_quality", models.JSONField(blank=True, default=dict)),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("safe_error_summary", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "report",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="window_check",
                        to="microstructure.marketpilotreport",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="window_checks",
                        to="microstructure.marketpilotrun",
                    ),
                ),
            ],
            options={
                "ordering": ["-window_start", "-id"],
                "indexes": [
                    models.Index(
                        fields=["status", "-window_start"],
                        name="pilot_check_status_time_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("symbol", "window_start"),
                        name="pilot_check_symbol_window_unique",
                    )
                ],
            },
        ),
    ]
