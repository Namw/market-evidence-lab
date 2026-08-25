from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("microstructure", "0007_marketminute_top5_imbalance")]

    operations = [
        migrations.CreateModel(
            name="MarketPilotRun",
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
                ("prompt_version", models.CharField(max_length=80)),
                ("configured_model", models.CharField(max_length=160)),
                ("actual_models", models.JSONField(blank=True, default=list)),
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
                ("window_count", models.PositiveIntegerField(default=0)),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("total_tokens", models.PositiveIntegerField(default=0)),
                ("future_outcomes_excluded", models.BooleanField(default=True)),
                ("safe_error_summary", models.CharField(blank=True, max_length=500)),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-started_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["symbol", "-started_at"],
                        name="pilot_run_sym_start_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="MarketPilotReport",
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
                ("window_start", models.DateTimeField()),
                ("window_end", models.DateTimeField()),
                (
                    "selection_reason",
                    models.CharField(
                        choices=[
                            ("absolute_return_ge_2pct", "候选异动"),
                            ("calm_control", "平静对照"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "mechanism",
                    models.CharField(
                        choices=[
                            ("trend_expansion", "趋势扩张"),
                            ("short_squeeze", "空头回补"),
                            ("long_liquidation", "多头去杠杆"),
                            ("technical_rebound", "技术反弹"),
                            ("technical_pullback", "技术回调"),
                            ("liquidity_jump", "流动性跳变"),
                            ("mixed", "混合机制"),
                            ("insufficient_evidence", "证据不足"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "confidence",
                    models.CharField(
                        choices=[("low", "低"), ("medium", "中"), ("high", "高")],
                        max_length=20,
                    ),
                ),
                ("input_snapshot", models.JSONField()),
                ("ai_analysis", models.JSONField()),
                ("future_outcomes", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reports",
                        to="microstructure.marketpilotrun",
                    ),
                ),
            ],
            options={
                "ordering": ["-window_start", "-id"],
                "indexes": [
                    models.Index(
                        fields=["run", "-window_start"],
                        name="pilot_report_run_time_idx",
                    ),
                    models.Index(
                        fields=["mechanism", "confidence"],
                        name="pilot_report_mech_conf_idx",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="marketpilotrun",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "running")),
                fields=("symbol",),
                name="pilot_one_running_symbol",
            ),
        ),
        migrations.AddConstraint(
            model_name="marketpilotreport",
            constraint=models.UniqueConstraint(
                fields=("run", "window_start"),
                name="pilot_report_run_window_unique",
            ),
        ),
    ]
