from django.db import models


class Kline(models.Model):
    class Exchange(models.TextChoices):
        BINANCE = "binance", "Binance"

    class MarketType(models.TextChoices):
        USD_M_FUTURES = "usd_m_futures", "USD-M Futures"

    class Interval(models.TextChoices):
        ONE_DAY = "1d", "1d"
        ONE_HOUR = "1h", "1h"
        FIVE_MINUTES = "5m", "5m"

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
    class Period(models.TextChoices):
        ONE_HOUR = "1h", "1h"
        FIVE_MINUTES = "5m", "5m"

    exchange = models.CharField(max_length=20, choices=Kline.Exchange.choices)
    market_type = models.CharField(max_length=30, choices=Kline.MarketType.choices)
    symbol = models.CharField(max_length=20)
    period = models.CharField(max_length=5, choices=Period.choices)
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


class DeribitVolatilityIndexCandle(models.Model):
    class Currency(models.TextChoices):
        ETH = "ETH", "ETH"

    class Resolution(models.TextChoices):
        ONE_HOUR = "1h", "1h"

    currency = models.CharField(max_length=10, choices=Currency.choices)
    resolution = models.CharField(max_length=5, choices=Resolution.choices)
    open_time = models.DateTimeField()
    close_time = models.DateTimeField()
    open = models.DecimalField(max_digits=40, decimal_places=18)
    high = models.DecimalField(max_digits=40, decimal_places=18)
    low = models.DecimalField(max_digits=40, decimal_places=18)
    close = models.DecimalField(max_digits=40, decimal_places=18)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["open_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["currency", "resolution", "open_time"],
                name="unique_deribit_dvol_candle",
            ),
            models.CheckConstraint(
                condition=models.Q(high__gte=models.F("low")),
                name="deribit_dvol_high_gte_low",
            ),
        ]
        indexes = [
            models.Index(
                fields=["currency", "resolution", "open_time"],
                name="dvol_curr_res_time_idx",
            )
        ]

    def __str__(self) -> str:
        return f"deribit:{self.currency}:{self.resolution}:{self.open_time.isoformat()}"


class DeribitOptionInstrument(models.Model):
    class OptionType(models.TextChoices):
        CALL = "call", "Call"
        PUT = "put", "Put"

    instrument_id = models.PositiveBigIntegerField(unique=True)
    instrument_name = models.CharField(max_length=80, unique=True)
    base_currency = models.CharField(max_length=10)
    quote_currency = models.CharField(max_length=10)
    settlement_currency = models.CharField(max_length=10)
    option_type = models.CharField(max_length=10, choices=OptionType.choices)
    strike = models.DecimalField(max_digits=40, decimal_places=18)
    expiration_time = models.DateTimeField()
    creation_time = models.DateTimeField()
    contract_size = models.DecimalField(max_digits=40, decimal_places=18)
    is_active = models.BooleanField(default=True)
    state = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["expiration_time", "strike", "option_type"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(strike__gt=0),
                name="deribit_option_strike_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(contract_size__gt=0),
                name="deribit_contract_size_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["base_currency", "is_active", "expiration_time"],
                name="deribit_opt_active_exp_idx",
            )
        ]

    def __str__(self) -> str:
        return self.instrument_name


class DeribitOptionMarketSnapshot(models.Model):
    instrument = models.ForeignKey(
        DeribitOptionInstrument,
        on_delete=models.PROTECT,
        related_name="market_snapshots",
    )
    observed_at = models.DateTimeField()
    source_timestamp = models.DateTimeField()
    underlying_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    mark_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    mark_iv = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    bid_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    ask_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    mid_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    last_price = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    open_interest = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    volume_24h = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    volume_usd_24h = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    interest_rate = models.DecimalField(
        max_digits=40, decimal_places=18, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["observed_at", "instrument__expiration_time", "instrument__strike"]
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "observed_at"],
                name="unique_deribit_option_snapshot",
            ),
            models.CheckConstraint(
                condition=models.Q(open_interest__isnull=True)
                | models.Q(open_interest__gte=0),
                name="deribit_option_oi_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(mark_iv__isnull=True) | models.Q(mark_iv__gte=0),
                name="deribit_option_iv_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["observed_at"], name="deribit_opt_obs_idx"),
            models.Index(
                fields=["instrument", "-observed_at"],
                name="deribit_opt_inst_obs_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.instrument.instrument_name}:{self.observed_at.isoformat()}"
