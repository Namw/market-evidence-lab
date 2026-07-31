import apps.research_cases.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("market_monitoring", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("exchange", models.CharField(choices=[("binance", "Binance")], max_length=20)),
                ("market_type", models.CharField(choices=[("usd_m_futures", "USD-M Futures")], max_length=30)),
                ("symbol", models.CharField(max_length=20)),
                ("interval", models.CharField(choices=[("1d", "1d")], max_length=5)),
                ("event_time", models.DateTimeField()),
                ("title", models.CharField(max_length=200)),
                ("anomaly_signals_snapshot", models.JSONField(default=apps.research_cases.models.empty_signals_snapshot)),
                ("calculation_snapshot", models.JSONField(default=apps.research_cases.models.empty_calculation_snapshot)),
                ("open", models.DecimalField(decimal_places=18, max_digits=40)),
                ("high", models.DecimalField(decimal_places=18, max_digits=40)),
                ("low", models.DecimalField(decimal_places=18, max_digits=40)),
                ("close", models.DecimalField(decimal_places=18, max_digits=40)),
                ("volume", models.DecimalField(decimal_places=18, max_digits=40)),
                ("price_change_pct", models.DecimalField(decimal_places=18, max_digits=40)),
                ("amplitude_pct", models.DecimalField(decimal_places=18, max_digits=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_finding", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="research_cases", to="market_monitoring.marketanomalyfinding")),
            ],
            options={
                "ordering": ["-event_time", "-created_at"],
                "indexes": [models.Index(fields=["symbol", "interval", "-event_time"], name="research_case_event_idx")],
                "constraints": [models.UniqueConstraint(fields=("exchange", "market_type", "symbol", "interval", "event_time"), name="unique_research_case_market_event")],
            },
        ),
    ]
