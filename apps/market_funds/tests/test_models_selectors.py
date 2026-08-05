from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.market_funds.models import AddressBalanceDaily, AddressEntity, EtfFlowDaily, StablecoinSupplyDaily
from apps.market_funds.selectors import address_metrics, etf_metrics, stablecoin_metrics


NOW = datetime(2026, 8, 5, tzinfo=UTC)


class ModelAndMetricTests(TestCase):
    def stable(self, day, value):
        return StablecoinSupplyDaily.objects.create(
            observation_date=day, chain="Ethereum", stablecoin_symbol="",
            circulating_supply=value, circulating_supply_usd=value,
            source_url="https://example.test", retrieved_at=NOW,
        )

    def test_stablecoin_1d_7d_30d_metrics_are_recomputable(self):
        current = date(2026, 8, 5)
        for days, value in ((30, 70), (7, 90), (1, 99), (0, 100)):
            self.stable(current - timedelta(days=days), value)
        metrics = stablecoin_metrics()
        self.assertEqual(metrics["change_1d"], 1)
        self.assertEqual(metrics["change_7d"], 10)
        self.assertEqual(metrics["change_30d"], 30)

    def test_etf_trading_day_cumulative_and_ticker_contribution(self):
        for offset, total in enumerate((1, 2, 3, 4, 5)):
            day = date(2026, 8, 1) + timedelta(days=offset)
            EtfFlowDaily.objects.create(trade_date=day, ticker="TOTAL", flow_usd=total, raw_value=str(total), is_total=True, source_url="https://example.test", retrieved_at=NOW)
            EtfFlowDaily.objects.create(trade_date=day, ticker="ETHA", flow_usd=total, raw_value=str(total), source_url="https://example.test", retrieved_at=NOW)
        metrics = etf_metrics()
        self.assertEqual(metrics["cumulative_5d"], 15)
        self.assertEqual(metrics["contributions"][0].ticker, "ETHA")

    def test_address_metadata_is_not_duplicated_and_changes_recompute(self):
        entity = AddressEntity.objects.create(address="0x" + "A" * 40, public_label="Label", label_source="fixture", first_seen_at=NOW, last_seen_at=NOW)
        AddressBalanceDaily.objects.create(snapshot_date=date(2026, 8, 4), address=entity, balance_eth=100, rank=2, observed_at=NOW)
        AddressBalanceDaily.objects.create(snapshot_date=date(2026, 8, 5), address=entity, balance_eth=125, rank=1, observed_at=NOW)
        metrics = address_metrics()
        self.assertEqual(AddressEntity.objects.count(), 1)
        self.assertEqual(metrics["rows"][0]["balance_change_1d"], 25)
        self.assertEqual(metrics["rows"][0]["rank_change"], 1)

    def test_snapshot_unique_constraint(self):
        entity = AddressEntity.objects.create(address="0x" + "b" * 40, first_seen_at=NOW, last_seen_at=NOW)
        AddressBalanceDaily.objects.create(snapshot_date=date(2026, 8, 5), address=entity, balance_eth=1, rank=1, observed_at=NOW)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AddressBalanceDaily.objects.create(snapshot_date=date(2026, 8, 5), address=entity, balance_eth=2, rank=2, observed_at=NOW)
