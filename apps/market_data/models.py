from django.db import models


class Kline(models.Model):
    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"

    class Interval(models.TextChoices):
        ONE_DAY = "1d", "1d"
        ONE_HOUR = "1h", "1h"

    exchange = models.CharField(max_length=20, choices=Exchange.choices)
    market_type = models.CharField(max_length=30, choices=MarketType.choices)
    symbol = models.CharField(max_length=20)
    interval = models.CharField(max_length=5, choices=Interval.choices)
    open_time = models.DateTimeField()
    close_time = models.DateTimeField()
    open = models.DecimalField(max_digits=40, decimal_places=18)
    high = models.DecimalField(max_digits=40, decimal_places=18)
    low = models.DecimalField(max_digits=40, decimal_places=18)
    close = models.DecimalField(max_digits=40, decimal_places=18)
    volume = models.DecimalField(max_digits=40, decimal_places=18)
    quote_volume = models.DecimalField(max_digits=40, decimal_places=18)
    trade_count = models.PositiveBigIntegerField()
    taker_buy_base_volume = models.DecimalField(max_digits=40, decimal_places=18)
    taker_buy_quote_volume = models.DecimalField(max_digits=40, decimal_places=18)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["open_time"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "exchange",
                    "market_type",
                    "symbol",
                    "interval",
                    "open_time",
                ],
                name="unique_kline_market_open_time",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "interval", "open_time"],
                name="kline_sym_int_open_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.exchange}:{self.symbol}:{self.interval}:{self.open_time.isoformat()}"
