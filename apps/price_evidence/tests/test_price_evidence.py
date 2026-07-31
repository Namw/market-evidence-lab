from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.market_data.models import Kline
from apps.market_monitoring.models import MarketAnomalyFinding, MarketScanRun
from apps.price_evidence.models import PriceEvidence
from apps.price_evidence.services import generate_price_evidence
from apps.research_cases.services import get_or_create_case_from_finding


DAY = datetime(2024, 4, 12, tzinfo=UTC)
CLOSES = [
    Decimal(value)
    for value in (
        "101", "102", "104", "106", "108", "110", "109", "108",
        "108", "106", "95", "96", "94", "92", "91", "93",
        "95", "97", "99", "101", "104", "106", "108", "110",
    )
]


def create_kline(interval, open_time, open_price, high, low, close, volume):
    return Kline.objects.create(
        exchange=Kline.Exchange.BINANCE,
        market_type=Kline.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval=interval,
        open_time=open_time,
        close_time=open_time + (timedelta(days=1) if interval == "1d" else timedelta(hours=1)) - timedelta(milliseconds=1),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=volume * close,
        trade_count=100,
        taker_buy_base_volume=volume / Decimal("2"),
        taker_buy_quote_volume=volume * close / Decimal("2"),
    )


def make_research_case():
    daily_kline = create_kline(
        Kline.Interval.ONE_DAY,
        DAY,
        Decimal("100"),
        Decimal("115"),
        Decimal("90"),
        Decimal("110"),
        Decimal("3000"),
    )
    run = MarketScanRun.objects.create(
        exchange=MarketScanRun.Exchange.BINANCE,
        market_type=MarketScanRun.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval=MarketScanRun.Interval.ONE_DAY,
        range_start=DAY,
        range_end=DAY + timedelta(days=1),
        rules_version="v1",
        rules_snapshot={"version": "v1"},
        status=MarketScanRun.Status.SUCCESS,
        expected_count=1,
        actual_count=1,
        evaluated_count=1,
        anomaly_day_count=1,
        signal_count=1,
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    finding = MarketAnomalyFinding.objects.create(
        run=run,
        kline=daily_kline,
        open_time=DAY,
        open=Decimal("100"),
        high=Decimal("115"),
        low=Decimal("90"),
        close=Decimal("110"),
        volume=Decimal("3000"),
        price_change_pct=Decimal("10"),
        amplitude_pct=Decimal("25"),
        upper_wick_range_ratio=Decimal("0.2"),
        lower_wick_range_ratio=Decimal("0.4"),
        signals=[
            {
                "type": "abnormal_change_up",
                "direction": "up",
                "metric": {"value": "10"},
                "threshold": {"value": "5"},
            }
        ],
    )
    return get_or_create_case_from_finding(finding)[0]


def make_hourly_klines(skip_hour=None):
    klines = []
    open_price = Decimal("100")
    for hour, close in enumerate(CLOSES):
        high = max(open_price, close) + Decimal("1")
        low = min(open_price, close) - Decimal("1")
        if hour == 5:
            high = Decimal("115")
        if hour == 14:
            low = Decimal("90")
        if hour != skip_hour:
            klines.append(
                create_kline(
                    Kline.Interval.ONE_HOUR,
                    DAY + timedelta(hours=hour),
                    open_price,
                    high,
                    low,
                    close,
                    Decimal((hour + 1) * 10),
                )
            )
        open_price = close
    return klines


class CompletePriceEvidenceTests(TestCase):
    def setUp(self):
        self.research_case = make_research_case()
        self.klines = make_hourly_klines()

    def generate(self):
        return generate_price_evidence(self.research_case.pk)[0]

    def test_complete_generation_and_daily_consistency(self):
        evidence = self.generate()

        self.assertEqual(evidence.quality_status, PriceEvidence.QualityStatus.COMPLETE)
        self.assertEqual(evidence.expected_count, 24)
        self.assertEqual(evidence.actual_count, 24)
        self.assertEqual(evidence.missing_open_times, [])
        for field in ("open", "high", "low", "close", "volume"):
            self.assertTrue(evidence.daily_consistency_snapshot[field]["matches"])

    def test_freezes_all_twenty_four_hourly_ohlcv_rows_as_strings(self):
        evidence = self.generate()

        self.assertEqual(len(evidence.hourly_klines_snapshot), 24)
        first = evidence.hourly_klines_snapshot[0]
        self.assertEqual(
            set(first),
            {"open_time", "open", "high", "low", "close", "volume"},
        )
        self.assertEqual(first["open_time"], "2024-04-12T00:00:00Z")
        self.assertEqual(first["open"], "100.000000000000000000")
        self.assertIsInstance(first["volume"], str)

    def test_high_low_order_and_earliest_tie(self):
        self.klines[6].high = Decimal("115")
        self.klines[6].save(update_fields=["high", "updated_at"])
        self.klines[15].low = Decimal("90")
        self.klines[15].save(update_fields=["low", "updated_at"])

        metrics = self.generate().metrics_snapshot

        self.assertEqual(metrics["high_low"]["order"], "high_before_low")
        self.assertEqual(metrics["high_low"]["highest"]["open_time"], "2024-04-12T05:00:00Z")
        self.assertEqual(metrics["high_low"]["lowest"]["open_time"], "2024-04-12T14:00:00Z")

    def test_largest_hourly_change_uses_absolute_value(self):
        largest = self.generate().metrics_snapshot["largest_hourly_change"]

        self.assertEqual(largest["open_time"], "2024-04-12T10:00:00Z")
        self.assertEqual(largest["direction"], "down")
        self.assertEqual(
            Decimal(largest["change_pct"]),
            (Decimal("95") - Decimal("106")) / Decimal("106") * Decimal("100"),
        )

    def test_largest_hourly_change_tie_uses_earliest_hour(self):
        tied = self.klines[11]
        tied.open = Decimal("106")
        tied.high = Decimal("107")
        tied.low = Decimal("94")
        tied.close = Decimal("95")
        tied.save(update_fields=["open", "high", "low", "close", "updated_at"])

        largest = self.generate().metrics_snapshot["largest_hourly_change"]

        self.assertEqual(largest["open_time"], "2024-04-12T10:00:00Z")

    def test_first_time_reaching_eighty_percent_of_final_net_change(self):
        metric = self.generate().metrics_snapshot["net_change_eighty_percent"]

        self.assertTrue(metric["available"])
        self.assertEqual(metric["target_price"], "108.0000000000000000000")
        self.assertEqual(metric["hourly_open_time"], "2024-04-12T04:00:00Z")
        self.assertEqual(metric["close_observed_at"], "2024-04-12T05:00:00Z")

    def test_up_down_flat_hour_counts(self):
        counts = self.generate().metrics_snapshot["hour_counts"]

        self.assertEqual(counts, {"up": 16, "down": 7, "flat": 1})

    def test_maximum_drawdown_and_rebound(self):
        metrics = self.generate().metrics_snapshot

        self.assertEqual(Decimal(metrics["max_drawdown"]["amount"]), Decimal("19"))
        self.assertEqual(metrics["max_drawdown"]["from_time"], "2024-04-12T06:00:00Z")
        self.assertEqual(metrics["max_drawdown"]["to_time"], "2024-04-12T15:00:00Z")
        self.assertEqual(Decimal(metrics["max_rebound"]["amount"]), Decimal("19"))
        self.assertEqual(metrics["max_rebound"]["from_time"], "2024-04-12T15:00:00Z")
        self.assertEqual(metrics["max_rebound"]["to_time"], "2024-04-13T00:00:00Z")

    def test_close_retention_rate(self):
        metric = self.generate().metrics_snapshot["close_retention"]

        self.assertEqual(metric["direction"], "up")
        self.assertEqual(
            Decimal(metric["rate_pct"]),
            Decimal("10") / Decimal("15") * Decimal("100"),
        )

    def test_flat_day_has_no_eighty_percent_time_or_retention_rate(self):
        self.research_case.close = Decimal("100")
        self.research_case.save(update_fields=["close", "updated_at"])
        last = self.klines[-1]
        last.close = Decimal("100")
        last.low = Decimal("99")
        last.save(update_fields=["close", "low", "updated_at"])

        evidence = self.generate()

        self.assertEqual(evidence.quality_status, PriceEvidence.QualityStatus.COMPLETE)
        self.assertFalse(evidence.metrics_snapshot["net_change_eighty_percent"]["available"])
        self.assertIsNone(evidence.metrics_snapshot["close_retention"]["rate_pct"])

    def test_volume_distribution(self):
        metric = self.generate().metrics_snapshot["volume_distribution"]

        self.assertTrue(metric["available"])
        self.assertEqual(metric["maximum_hour"]["open_time"], "2024-04-12T23:00:00Z")
        self.assertEqual(Decimal(metric["maximum_hour"]["volume"]), Decimal("240"))
        self.assertEqual(Decimal(metric["top_three_share_pct"]), Decimal("23"))

    def test_repeated_generation_updates_single_current_evidence(self):
        first, first_created = generate_price_evidence(self.research_case.pk)
        self.assertTrue(first_created)
        self.klines[0].volume += Decimal("5")
        self.klines[0].save(update_fields=["volume", "updated_at"])
        self.klines[1].volume -= Decimal("5")
        self.klines[1].save(update_fields=["volume", "updated_at"])

        second, second_created = generate_price_evidence(self.research_case.pk)

        self.assertFalse(second_created)
        self.assertEqual(PriceEvidence.objects.count(), 1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.quality_status, PriceEvidence.QualityStatus.COMPLETE)
        self.assertEqual(
            Decimal(second.hourly_klines_snapshot[0]["volume"]),
            Decimal("15"),
        )


class PriceEvidenceQualityTests(TestCase):
    def setUp(self):
        self.research_case = make_research_case()

    def test_missing_one_hour_is_partial_and_records_exact_gap(self):
        make_hourly_klines(skip_hour=7)

        evidence, _ = generate_price_evidence(self.research_case.pk)

        self.assertEqual(evidence.quality_status, PriceEvidence.QualityStatus.PARTIAL)
        self.assertEqual(evidence.actual_count, 23)
        self.assertEqual(evidence.missing_open_times, ["2024-04-12T07:00:00Z"])
        self.assertEqual(evidence.metrics_snapshot, {})

    def test_adjacent_day_data_does_not_fill_missing_hour(self):
        make_hourly_klines(skip_hour=0)
        create_kline(
            Kline.Interval.ONE_HOUR,
            DAY - timedelta(hours=1),
            Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100"), Decimal("10"),
        )
        create_kline(
            Kline.Interval.ONE_HOUR,
            DAY + timedelta(days=1),
            Decimal("110"), Decimal("111"), Decimal("109"), Decimal("110"), Decimal("10"),
        )

        evidence, _ = generate_price_evidence(self.research_case.pk)

        self.assertEqual(evidence.actual_count, 23)
        self.assertEqual(evidence.missing_open_times, ["2024-04-12T00:00:00Z"])

    def test_no_hourly_data_is_unavailable(self):
        evidence, _ = generate_price_evidence(self.research_case.pk)

        self.assertEqual(evidence.quality_status, PriceEvidence.QualityStatus.UNAVAILABLE)
        self.assertEqual(evidence.actual_count, 0)
        self.assertEqual(len(evidence.missing_open_times), 24)
        self.assertEqual(evidence.metrics_snapshot, {})

    def test_full_but_daily_ohlcv_mismatch_is_inconsistent(self):
        klines = make_hourly_klines()
        klines[5].high = Decimal("116")
        klines[5].save(update_fields=["high", "updated_at"])

        evidence, _ = generate_price_evidence(self.research_case.pk)

        self.assertEqual(evidence.quality_status, PriceEvidence.QualityStatus.INCONSISTENT)
        self.assertFalse(evidence.daily_consistency_snapshot["high"]["matches"])
        self.assertEqual(evidence.metrics_snapshot, {})

    def test_unsafe_ohlc_is_inconsistent_and_has_no_metrics(self):
        klines = make_hourly_klines()
        klines[0].open = Decimal("0")
        klines[0].save(update_fields=["open", "updated_at"])

        evidence, _ = generate_price_evidence(self.research_case.pk)

        self.assertEqual(evidence.quality_status, PriceEvidence.QualityStatus.INCONSISTENT)
        self.assertIn(
            "non_positive_ohlc:2024-04-12T00:00:00Z",
            evidence.daily_consistency_snapshot["safety_issues"],
        )
        self.assertEqual(evidence.metrics_snapshot, {})


class PriceEvidenceViewTests(TestCase):
    def setUp(self):
        self.research_case = make_research_case()
        self.generate_url = reverse("price_evidence:generate", args=[self.research_case.pk])
        self.detail_url = reverse("research_cases:detail", args=[self.research_case.pk])

    def test_generation_endpoint_rejects_get(self):
        response = self.client.get(self.generate_url)

        self.assertEqual(response.status_code, 405)
        self.assertFalse(PriceEvidence.objects.exists())

    def test_generation_endpoint_requires_csrf(self):
        response = Client(enforce_csrf_checks=True).post(self.generate_url)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PriceEvidence.objects.exists())

    def test_post_generates_and_redirects_to_price_section(self):
        make_hourly_klines()

        response = self.client.post(self.generate_url)

        self.assertRedirects(
            response,
            f"{self.detail_url}#price-evidence",
            fetch_redirect_response=False,
        )
        self.assertEqual(PriceEvidence.objects.count(), 1)

    def test_non_complete_page_does_not_render_complete_day_conclusions(self):
        make_hourly_klines(skip_hour=7)
        generate_price_evidence(self.research_case.pk)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "部分缺失")
        self.assertContains(response, "2024-04-12T07:00:00Z")
        self.assertNotContains(response, "首次达到最终净涨跌80%的价位")
        self.assertNotContains(response, "前3小时成交量集中度")

    def test_detail_page_order_and_local_chart_asset(self):
        make_hourly_klines()
        generate_price_evidence(self.research_case.pk)

        response = self.client.get(self.detail_url)
        html = response.content.decode()
        ordered_ids = (
            'id="anomaly-overview"',
            'id="price-evidence"',
            'id="derivatives-evidence"',
            'id="news-evidence"',
            'id="historical-evidence"',
            'id="ai-report"',
            'id="source-audit"',
        )

        positions = [html.index(marker) for marker in ordered_ids]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, "price_evidence/js/charts.js")
        self.assertNotContains(response, "cdn")
        self.assertContains(response, "1小时蜡烛图")
        self.assertContains(response, "1h成交量")

    def test_primary_human_overview_formats_decimals(self):
        response = self.client.get(self.detail_url)
        html = response.content.decode()
        overview = html[html.index('id="anomaly-overview"'):html.index('id="price-evidence"')]

        self.assertIn("100", overview)
        self.assertIn("3,000.00", overview)
        self.assertIn("10.00%", overview)
        self.assertNotIn("100.000000000000000000", overview)
        self.assertNotIn("10.000000000000000000%", overview)
