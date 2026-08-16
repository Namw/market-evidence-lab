from django.db import models


class OrderBookSnapshot(models.Model):
    symbol = models.CharField(max_length=20)
    sampled_at = models.DateTimeField()
    event_time = models.DateTimeField()
    received_at = models.DateTimeField()
    update_id = models.PositiveBigIntegerField()

    best_bid = models.DecimalField(max_digits=40, decimal_places=18)
    best_ask = models.DecimalField(max_digits=40, decimal_places=18)
    mid_price = models.DecimalField(max_digits=40, decimal_places=18)
    spread = models.DecimalField(max_digits=40, decimal_places=18)
    spread_bps = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )

    bid_depth_top5_quote = models.DecimalField(max_digits=40, decimal_places=18)
    ask_depth_top5_quote = models.DecimalField(max_digits=40, decimal_places=18)
    bid_depth_top10_quote = models.DecimalField(max_digits=40, decimal_places=18)
    ask_depth_top10_quote = models.DecimalField(max_digits=40, decimal_places=18)
    bid_depth_top20_quote = models.DecimalField(max_digits=40, decimal_places=18)
    ask_depth_top20_quote = models.DecimalField(max_digits=40, decimal_places=18)

    imbalance_top5 = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top10 = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top20 = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sampled_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "sampled_at"],
                name="unique_orderbook_snapshot_time",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "-sampled_at"],
                name="orderbook_symbol_time_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:{self.sampled_at.isoformat()}"


class OrderBookFiveMinuteSummary(models.Model):
    symbol = models.CharField(max_length=20)
    interval_start = models.DateTimeField()
    interval_end = models.DateTimeField()

    mid_open = models.DecimalField(max_digits=40, decimal_places=18)
    mid_high = models.DecimalField(max_digits=40, decimal_places=18)
    mid_low = models.DecimalField(max_digits=40, decimal_places=18)
    mid_close = models.DecimalField(max_digits=40, decimal_places=18)

    spread_bps_mean = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    spread_bps_max = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    spread_bps_end = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )

    bid_depth_top5_quote_mean = models.DecimalField(max_digits=40, decimal_places=18)
    ask_depth_top5_quote_mean = models.DecimalField(max_digits=40, decimal_places=18)
    bid_depth_top10_quote_mean = models.DecimalField(max_digits=40, decimal_places=18)
    ask_depth_top10_quote_mean = models.DecimalField(max_digits=40, decimal_places=18)
    bid_depth_top20_quote_mean = models.DecimalField(max_digits=40, decimal_places=18)
    ask_depth_top20_quote_mean = models.DecimalField(max_digits=40, decimal_places=18)

    imbalance_top5_mean = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top5_end = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top10_mean = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top10_end = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top20_mean = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )
    imbalance_top20_end = models.DecimalField(
        max_digits=40,
        decimal_places=18,
        null=True,
        blank=True,
    )

    snapshot_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["interval_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "interval_start"],
                name="unique_orderbook_5m_summary",
            )
        ]
        indexes = [
            models.Index(
                fields=["symbol", "-interval_start"],
                name="orderbook_5m_symbol_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:5m:{self.interval_start.isoformat()}"
