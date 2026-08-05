from django.db import models


class StablecoinSupplyDaily(models.Model):
    observation_date = models.DateField(db_index=True)
    chain = models.CharField(max_length=32, default="Ethereum")
    stablecoin_symbol = models.CharField(max_length=20, blank=True, default="")
    circulating_supply = models.DecimalField(max_digits=40, decimal_places=8)
    circulating_supply_usd = models.DecimalField(max_digits=40, decimal_places=8)
    minted_supply_usd = models.DecimalField(
        max_digits=40, decimal_places=8, null=True, blank=True
    )
    bridged_supply_usd = models.DecimalField(
        max_digits=40, decimal_places=8, null=True, blank=True
    )
    source = models.CharField(max_length=40, default="DeFiLlama")
    source_url = models.URLField(max_length=300)
    retrieved_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-observation_date", "stablecoin_symbol"]
        constraints = [
            models.UniqueConstraint(
                fields=["observation_date", "chain", "stablecoin_symbol"],
                name="fund_stablecoin_day_chain_symbol_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["chain", "stablecoin_symbol", "-observation_date"],
                name="fund_stablecoin_lookup_idx",
            )
        ]


class EtfFlowDaily(models.Model):
    trade_date = models.DateField(db_index=True)
    ticker = models.CharField(max_length=20)
    flow_usd = models.DecimalField(
        max_digits=30, decimal_places=2, null=True, blank=True
    )
    raw_value = models.CharField(max_length=80, blank=True)
    is_total = models.BooleanField(default=False)
    source = models.CharField(max_length=40, default="Farside")
    source_url = models.URLField(max_length=300)
    retrieved_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-trade_date", "is_total", "ticker"]
        constraints = [
            models.UniqueConstraint(
                fields=["trade_date", "ticker"],
                name="fund_etf_day_ticker_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["ticker", "-trade_date"], name="fund_etf_lookup_idx"
            )
        ]


class AddressEntity(models.Model):
    class AddressType(models.TextChoices):
        UNKNOWN = "unknown", "未知"
        EOA = "eoa", "EOA"
        CONTRACT = "contract", "合约"

    address = models.CharField(max_length=42, unique=True)
    public_label = models.CharField(max_length=300, blank=True)
    address_type = models.CharField(
        max_length=20, choices=AddressType.choices, default=AddressType.UNKNOWN
    )
    label_source = models.CharField(max_length=40, blank=True)
    label_is_public = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["address"]

    def save(self, *args, **kwargs):
        self.address = self.address.lower()
        super().save(*args, **kwargs)


class AddressBalanceDaily(models.Model):
    snapshot_date = models.DateField(db_index=True)
    address = models.ForeignKey(
        AddressEntity, on_delete=models.PROTECT, related_name="daily_balances"
    )
    balance_eth = models.DecimalField(max_digits=50, decimal_places=18)
    rank = models.PositiveSmallIntegerField()
    block_number = models.PositiveBigIntegerField(null=True, blank=True)
    observed_at = models.DateTimeField()
    source = models.CharField(max_length=40, default="Etherscan")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_date", "rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot_date", "address"],
                name="fund_address_snapshot_unique",
            ),
            models.UniqueConstraint(
                fields=["snapshot_date", "rank"],
                name="fund_address_rank_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["address", "-snapshot_date"],
                name="fund_address_history_idx",
            )
        ]


class SourceDiagnostic(models.Model):
    source = models.CharField(max_length=40, unique=True)
    source_url = models.URLField(max_length=300)
    response_sha256 = models.CharField(max_length=64, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    retrieved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    policy_status = models.CharField(max_length=30, default="allowed")
    policy_note = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


def empty_quality_details():
    return {
        "duplicate_keys": [],
        "missing_dates": [],
        "invalid_rows": [],
        "sequence_issues": [],
        "no_new_data": False,
    }


class FundDataInspectionRun(models.Model):
    class TaskType(models.TextChoices):
        STABLECOIN = "stablecoin", "稳定币供应"
        ETF = "etf", "ETF 每日资金流"
        ADDRESSES = "addresses", "公开地址余额"

    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "待判定"
        PASSED = "passed", "通过"
        ISSUES = "issues", "发现问题"
        BLOCKED = "blocked", "来源策略阻止"

    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    source_collection_run = models.ForeignKey(
        "collection.CollectionRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fund_inspection_runs",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    quality_status = models.CharField(
        max_length=20, choices=QualityStatus.choices, default=QualityStatus.PENDING
    )
    actual_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    issue_count = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=empty_quality_details)
    safe_error_summary = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(
                fields=["task_type", "-started_at"], name="fund_quality_task_idx"
            )
        ]
