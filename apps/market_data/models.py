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


class OpenInterest(models.Model):
    exchange = models.CharField(max_length=20, choices=Kline.Exchange.choices)
    market_type = models.CharField(max_length=30, choices=Kline.MarketType.choices)
    symbol = models.CharField(max_length=20)
    period = models.CharField(max_length=5, choices=(("1h", "1h"),))
    timestamp = models.DateTimeField()
    sum_open_interest = models.DecimalField(max_digits=40, decimal_places=18)
    sum_open_interest_value = models.DecimalField(max_digits=40, decimal_places=18)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["timestamp"]
        constraints = [
            models.UniqueConstraint(
                fields=["exchange", "market_type", "symbol", "period", "timestamp"],
                name="unique_oi_market_period_time",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "period", "timestamp"],
                name="oi_sym_period_time_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.exchange}:{self.symbol}:{self.period}:{self.timestamp.isoformat()}"


class FundingRate(models.Model):
    exchange = models.CharField(max_length=20, choices=Kline.Exchange.choices)
    market_type = models.CharField(max_length=30, choices=Kline.MarketType.choices)
    symbol = models.CharField(max_length=20)
    funding_time = models.DateTimeField()
    funding_rate = models.DecimalField(max_digits=40, decimal_places=18)
    mark_price = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    rate_type = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["funding_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["exchange", "market_type", "symbol", "funding_time"],
                name="unique_funding_market_time",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "funding_time"],
                name="funding_sym_time_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.exchange}:{self.symbol}:{self.funding_time.isoformat()}"
