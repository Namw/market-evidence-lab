from django.db import models


def empty_signals_snapshot():
    return []


def empty_calculation_snapshot():
    return {}


class ResearchCase(models.Model):
    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"

    class Interval(models.TextChoices):
        ONE_DAY = "1d", "1d"

    source_finding = models.ForeignKey(
        "market_monitoring.MarketAnomalyFinding",
        on_delete=models.PROTECT,
        related_name="research_cases",
    )
    exchange = models.CharField(max_length=20, choices=Exchange.choices)
    market_type = models.CharField(max_length=30, choices=MarketType.choices)
    symbol = models.CharField(max_length=20)
    interval = models.CharField(max_length=5, choices=Interval.choices)
    event_time = models.DateTimeField()
    title = models.CharField(max_length=200)
    anomaly_signals_snapshot = models.JSONField(default=empty_signals_snapshot)
    calculation_snapshot = models.JSONField(default=empty_calculation_snapshot)
    open = models.DecimalField(max_digits=40, decimal_places=18)
    high = models.DecimalField(max_digits=40, decimal_places=18)
    low = models.DecimalField(max_digits=40, decimal_places=18)
    close = models.DecimalField(max_digits=40, decimal_places=18)
    volume = models.DecimalField(max_digits=40, decimal_places=18)
    price_change_pct = models.DecimalField(max_digits=40, decimal_places=18)
    amplitude_pct = models.DecimalField(max_digits=40, decimal_places=18)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-event_time", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "exchange",
                    "market_type",
                    "symbol",
                    "interval",
                    "event_time",
                ],
                name="unique_research_case_market_event",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "interval", "-event_time"],
                name="research_case_event_idx",
            )
        ]

    def __str__(self) -> str:
        return self.title
