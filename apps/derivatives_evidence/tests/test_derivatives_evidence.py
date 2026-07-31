from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.derivatives_evidence.models import DerivativesEvidence
from apps.derivatives_evidence.services import (
    calculate_funding_interval,
    funding_crowding,
    generate_derivatives_evidence,
    oi_direction,
    price_direction,
)
from apps.market_data.models import FundingRate, Kline, OpenInterest
from apps.market_monitoring.models import MarketAnomalyFinding, MarketScanRun
from apps.research_cases.services import get_or_create_case_from_finding
from apps.research_cases.templatetags.research_case_extras import human_funding


DAY = datetime(2026, 7, 12, tzinfo=UTC)


def kline(interval, open_time, open_price, close_price):
    duration = timedelta(days=1) if interval == "1d" else timedelta(hours=1)
    return Kline.objects.create(
        exchange=Kline.Exchange.BINANCE,
        market_type=Kline.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval=interval,
        open_time=open_time,
        close_time=open_time + duration - timedelta(milliseconds=1),
        open=open_price,
        high=max(open_price, close_price) + Decimal("1"),
        low=min(open_price, close_price) - Decimal("1"),
        close=close_price,
        volume=Decimal("100"),
        quote_volume=Decimal("300000"),
        trade_count=100,
        taker_buy_base_volume=Decimal("50"),
        taker_buy_quote_volume=Decimal("150000"),
    )


def research_case():
    daily = kline("1d", DAY, Decimal("100"), Decimal("110"))
    run = MarketScanRun.objects.create(
        exchange="binance", market_type="usd_m_futures", symbol="ETHUSDT", interval="1d",
        range_start=DAY, range_end=DAY + timedelta(days=1), rules_version="v1",
        rules_snapshot={}, status="success", expected_count=1, actual_count=1,
        evaluated_count=1, anomaly_day_count=1, signal_count=1,
        started_at=timezone.now(), finished_at=timezone.now(),
    )
    finding = MarketAnomalyFinding.objects.create(
        run=run, kline=daily, open_time=DAY, open=Decimal("100"), high=Decimal("111"),
        low=Decimal("99"), close=Decimal("110"), volume=Decimal("100"),
        price_change_pct=Decimal("10"), amplitude_pct=Decimal("12"),
        upper_wick_range_ratio=Decimal("0.1"), lower_wick_range_ratio=Decimal("0.1"),
        signals=[{"type": "abnormal_change_up", "direction": "up"}],
    )
    return get_or_create_case_from_finding(finding)[0]


def hourly_prices():
    for hour in range(24):
        open_price = Decimal("100")
        close_price = Decimal("101")
        if hour == 5:
            close_price = Decimal("105")
        if hour == 8:
            open_price, close_price = Decimal("200"), Decimal("210")
        kline("1h", DAY + timedelta(hours=hour), open_price, close_price)


def oi_points(*, skip=(), start=Decimal("1000"), end=Decimal("1100"), value_end=Decimal("3300000")):
    rows = []
    for hour in range(25):
        if hour in skip:
            continue
        quantity = start + (end - start) * Decimal(hour) / Decimal("24")
        value = Decimal("3000000") + (value_end - Decimal("3000000")) * Decimal(hour) / Decimal("24")
        rows.append(OpenInterest.objects.create(
            exchange="binance", market_type="usd_m_futures", symbol="ETHUSDT", period="1h",
            timestamp=DAY + timedelta(hours=hour), sum_open_interest=quantity,
            sum_open_interest_value=value,
        ))
    return rows


def funding_points(*, omit=()):
    rows = []
    for day_offset in (-1, 0, 1):
        for hour in (0, 8, 16):
            key = (day_offset, hour)
            if key in omit:
                continue
            rows.append(FundingRate.objects.create(
                exchange="binance", market_type="usd_m_futures", symbol="ETHUSDT",
                funding_time=DAY + timedelta(days=day_offset, hours=hour),
                funding_rate=Decimal("0.0001") + Decimal(day_offset + 1) * Decimal("0.00001") + Decimal(hour) * Decimal("0.000001"),
                mark_price=Decimal("3000"), rate_type="",
            ))
    return rows


class DerivativesRuleTests(TestCase):
    def test_oi_and_price_threshold_boundaries(self):
        self.assertEqual(oi_direction(Decimal("1")), "expansion")
        self.assertEqual(oi_direction(Decimal("-1")), "contraction")
        self.assertEqual(oi_direction(Decimal("0.999999")), "neutral")
        self.assertEqual(price_direction(Decimal("0.5")), "up")
        self.assertEqual(price_direction(Decimal("-0.5")), "down")
        self.assertEqual(price_direction(Decimal("0.499999")), "neutral")

    def test_funding_crowding_threshold_boundaries(self):
        self.assertEqual(funding_crowding(Decimal("0.0003")), "significant_positive")
        self.assertEqual(funding_crowding(Decimal("-0.0003")), "significant_negative")
        self.assertEqual(funding_crowding(Decimal("0.000299999")), "positive")

    def test_funding_interval_is_left_closed_right_open_and_reports_missing(self):
        before_end = FundingRate.objects.create(
            exchange="binance", market_type="usd_m_futures", symbol="ETHUSDT",
            funding_time=DAY - timedelta(hours=8), funding_rate=Decimal("0.0001"),
        )
        boundary = FundingRate.objects.create(
            exchange="binance", market_type="usd_m_futures", symbol="ETHUSDT",
            funding_time=DAY, funding_rate=Decimal("0.0002"),
        )
        records = list(FundingRate.objects.filter(funding_time__gte=DAY, funding_time__lt=DAY + timedelta(days=1)))
        snapshot = calculate_funding_interval("event_day", DAY, DAY + timedelta(days=1), records)

        self.assertEqual(snapshot["actual_count"], 1)
        self.assertEqual(snapshot["first"]["id"], boundary.pk)
        self.assertNotEqual(snapshot["first"]["id"], before_end.pk)
        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(len(snapshot["missing_funding_times"]), 2)
        self.assertIsNone(snapshot["average"])
        self.assertIsNone(snapshot["net_change"])
        self.assertIsNone(snapshot["trend"])
        self.assertIsNone(snapshot["average_direction"])
        self.assertIsNone(snapshot["first"]["crowding"])
        self.assertIn("停止输出", snapshot["status_reason"])

    def test_funding_settlement_millisecond_offset_matches_expected_hour(self):
        records = []
        for hour, milliseconds in ((0, 0), (8, 7), (16, 21)):
            records.append(FundingRate.objects.create(
                exchange="binance", market_type="usd_m_futures", symbol="ETHUSDT",
                funding_time=DAY + timedelta(hours=hour, milliseconds=milliseconds),
                funding_rate=Decimal("0.0001"),
            ))

        snapshot = calculate_funding_interval("event_day", DAY, DAY + timedelta(days=1), records)

        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["missing_funding_times"], [])
        self.assertIsNotNone(snapshot["average"])
        self.assertEqual(snapshot["trend"], "flat")
        self.assertEqual(snapshot["average_direction"], "positive")

    def test_funding_display_converts_raw_decimal_to_percentage(self):
        self.assertEqual(human_funding(Decimal("0.0001")), "0.010000%")
        self.assertEqual(human_funding(Decimal("-0.00025")), "-0.025000%")
        self.assertEqual(human_funding(None), "—")


class DerivativesEvidenceGenerationTests(TestCase):
    def setUp(self):
        self.case = research_case()

    def complete_sources(self):
        hourly_prices()
        oi_points()
        funding_points()

    def test_complete_25_point_oi_funding_and_joint_description(self):
        self.complete_sources()
        evidence, created = generate_derivatives_evidence(self.case.pk)
        snapshot = evidence.calculation_snapshot

        self.assertTrue(created)
        self.assertEqual(evidence.status, DerivativesEvidence.Status.COMPLETE)
        self.assertEqual(evidence.rule_version, "derivatives-evidence-v1")
        self.assertEqual(snapshot["oi"]["actual_count"], 25)
        self.assertEqual(snapshot["oi"]["quantity_direction"], "expansion")
        self.assertEqual(snapshot["joint_description"], "价格上涨且 OI 扩张，说明新增杠杆仓位参与上涨。")
        self.assertEqual([item["status"] for item in snapshot["funding_intervals"]], ["complete"] * 3)

    def test_missing_oi_start_end_or_middle_is_partial(self):
        for skipped in ((0,), (24,), (12,)):
            with self.subTest(skipped=skipped):
                OpenInterest.objects.all().delete()
                oi_points(skip=skipped)
                evidence, _ = generate_derivatives_evidence(self.case.pk)
                self.assertEqual(evidence.status, DerivativesEvidence.Status.PARTIAL)
                self.assertEqual(evidence.calculation_snapshot["oi"]["status"], "partial")
                self.assertIsNone(evidence.calculation_snapshot["oi"]["quantity_direction"])
                self.assertIsNone(evidence.calculation_snapshot["oi"]["value_direction"])
                self.assertIsNone(evidence.calculation_snapshot["joint_description"])

    def test_quantity_and_value_direction_divergence_suppresses_joint_judgment(self):
        hourly_prices()
        oi_points(value_end=Decimal("2700000"))
        funding_points()
        evidence, _ = generate_derivatives_evidence(self.case.pk)
        oi = evidence.calculation_snapshot["oi"]

        self.assertTrue(oi["quantity_value_divergence"])
        self.assertIsNone(oi["conclusion_direction"])
        self.assertIsNone(evidence.calculation_snapshot["joint_description"])
        self.assertIn("方向分歧", evidence.calculation_snapshot["joint_limitation"])

    def test_largest_price_hour_tie_uses_earliest_and_captures_oi_boundaries(self):
        hourly_prices()
        oi_points()
        funding_points()
        evidence, _ = generate_derivatives_evidence(self.case.pk)
        largest = evidence.calculation_snapshot["price"]["largest_absolute_hour"]

        self.assertEqual(largest["open_time"], "2026-07-12T05:00:00Z")
        self.assertTrue(largest["oi_boundaries_available"])
        self.assertIsNotNone(largest["oi_change"])

    def test_each_funding_interval_is_independent_when_one_settlement_is_missing(self):
        hourly_prices()
        oi_points()
        funding_points(omit=((0, 8),))
        evidence, _ = generate_derivatives_evidence(self.case.pk)
        statuses = {item["label"]: item["status"] for item in evidence.calculation_snapshot["funding_intervals"]}

        self.assertEqual(statuses, {"before": "complete", "event_day": "partial", "after": "complete"})
        self.assertEqual(evidence.status, DerivativesEvidence.Status.PARTIAL)

    def test_no_derivatives_source_is_unavailable_not_zero(self):
        evidence, _ = generate_derivatives_evidence(self.case.pk)

        self.assertEqual(evidence.status, DerivativesEvidence.Status.UNAVAILABLE)
        self.assertIsNone(evidence.calculation_snapshot["oi"]["quantity_change"])
        self.assertIn("当前无来源数据", evidence.calculation_snapshot["oi"]["status_reasons"][0])

    def test_repeated_generation_updates_one_snapshot(self):
        self.complete_sources()
        first, created = generate_derivatives_evidence(self.case.pk)
        first_time = first.calculated_at
        OpenInterest.objects.filter(timestamp=DAY + timedelta(days=1)).update(sum_open_interest=Decimal("1200"))
        second, created_again = generate_derivatives_evidence(self.case.pk)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(DerivativesEvidence.objects.count(), 1)
        self.assertGreaterEqual(second.calculated_at, first_time)
        self.assertEqual(second.calculation_snapshot["oi"]["end"]["sum_open_interest"], "1200.000000000000000000")

    @patch("apps.derivatives_evidence.services._build_snapshots", side_effect=RuntimeError("private data"))
    def test_calculation_exception_becomes_failed_safe_snapshot(self, _build):
        evidence, _ = generate_derivatives_evidence(self.case.pk)

        self.assertEqual(evidence.status, DerivativesEvidence.Status.FAILED)
        self.assertNotIn("private data", evidence.status_reason)


class DerivativesEvidenceViewTests(TestCase):
    def setUp(self):
        self.case = research_case()
        self.url = reverse("research_cases:detail", args=[self.case.pk])

    def test_no_data_page_has_collection_entry_and_default_fold_state(self):
        response = self.client.get(self.url)
        content = response.content.decode()

        self.assertContains(response, "尚未生成衍生品证据")
        self.assertContains(response, reverse("collection:derivatives"))
        self.assertIn('<details class="panel derivatives-evidence-panel" id="derivatives-evidence">', content)
        self.assertNotIn('<details class="panel derivatives-evidence-panel" id="derivatives-evidence" open', content)
        self.assertIn('<section class="panel price-evidence-panel"', content)

    def test_partial_and_complete_snapshots_render_readable_facts(self):
        oi_points(skip=(12,))
        partial, _ = generate_derivatives_evidence(self.case.pk)
        response = self.client.get(self.url)
        self.assertContains(response, partial.get_status_display())
        self.assertContains(response, "OI 限制")

        OpenInterest.objects.all().delete()
        hourly_prices()
        oi_points()
        funding_points()
        complete, _ = generate_derivatives_evidence(self.case.pk)
        response = self.client.get(self.url)
        self.assertContains(response, complete.get_status_display())
        self.assertContains(response, "价格上涨且 OI 扩张")
        self.assertContains(response, "异常前")
        self.assertContains(response, "0.010000%")

    def test_generate_post_is_idempotent(self):
        url = reverse("derivatives_evidence:generate", args=[self.case.pk])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(DerivativesEvidence.objects.count(), 1)
