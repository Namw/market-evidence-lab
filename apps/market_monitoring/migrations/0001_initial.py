import apps.market_monitoring.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("market_data", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketScanRun",
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
                    "exchange",
                    models.CharField(
                        choices=[("binance", "Binance")], max_length=20
                    ),
                ),
                (
                    "market_type",
                    models.CharField(
                        choices=[("usd_m_futures", "USD-M Futures")],
                        max_length=30,
                    ),
                ),
                ("symbol", models.CharField(max_length=20)),
                (
                    "interval",
                    models.CharField(choices=[("1d", "1d")], max_length=5),
                ),
                ("range_start", models.DateTimeField()),
                ("range_end", models.DateTimeField()),
                (
                    "trigger",
                    models.CharField(
                        choices=[("manual", "手工"), ("scheduled", "定时")],
                        default="manual",
                        max_length=20,
                    ),
                ),
                ("rules_version", models.CharField(default="v1", max_length=20)),
                (
                    "rules_snapshot",
                    models.JSONField(
                        default=apps.market_monitoring.models.empty_rules_snapshot
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
                ("expected_count", models.PositiveIntegerField(default=0)),
                ("actual_count", models.PositiveIntegerField(default=0)),
                ("evaluated_count", models.PositiveIntegerField(default=0)),
                ("missing_count", models.PositiveIntegerField(default=0)),
                ("skipped_invalid_count", models.PositiveIntegerField(default=0)),
                (
                    "volume_baseline_unavailable_count",
                    models.PositiveIntegerField(default=0),
                ),
                ("anomaly_day_count", models.PositiveIntegerField(default=0)),
                ("signal_count", models.PositiveIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-started_at"],
                "indexes": [
                    models.Index(
                        fields=["-started_at"], name="market_scan_started_idx"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="MarketAnomalyFinding",
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
                ("open_time", models.DateTimeField()),
                ("open", models.DecimalField(decimal_places=18, max_digits=40)),
                ("high", models.DecimalField(decimal_places=18, max_digits=40)),
                ("low", models.DecimalField(decimal_places=18, max_digits=40)),
                ("close", models.DecimalField(decimal_places=18, max_digits=40)),
                ("volume", models.DecimalField(decimal_places=18, max_digits=40)),
                (
                    "price_change_pct",
                    models.DecimalField(decimal_places=18, max_digits=40),
                ),
                (
                    "amplitude_pct",
                    models.DecimalField(decimal_places=18, max_digits=40),
                ),
                (
                    "volume_average_20",
                    models.DecimalField(
                        blank=True,
                        decimal_places=18,
                        max_digits=40,
                        null=True,
                    ),
                ),
                (
                    "volume_ratio",
                    models.DecimalField(
                        blank=True,
                        decimal_places=18,
                        max_digits=40,
                        null=True,
                    ),
                ),
                (
                    "upper_wick_body_ratio",
                    models.DecimalField(
                        blank=True,
                        decimal_places=18,
                        max_digits=40,
                        null=True,
                    ),
                ),
                (
                    "upper_wick_range_ratio",
                    models.DecimalField(decimal_places=18, max_digits=40),
                ),
                (
                    "lower_wick_body_ratio",
                    models.DecimalField(
                        blank=True,
                        decimal_places=18,
                        max_digits=40,
                        null=True,
                    ),
                ),
                (
                    "lower_wick_range_ratio",
                    models.DecimalField(decimal_places=18, max_digits=40),
                ),
                (
                    "signals",
                    models.JSONField(
                        default=apps.market_monitoring.models.empty_signals
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "kline",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="market_anomaly_findings",
                        to="market_data.kline",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="findings",
                        to="market_monitoring.marketscanrun",
                    ),
                ),
            ],
            options={
                "ordering": ["open_time"],
                "indexes": [
                    models.Index(
                        fields=["open_time"], name="market_finding_day_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("run", "open_time"),
                        name="unique_market_finding_run_day",
                    )
                ],
            },
        ),
    ]
