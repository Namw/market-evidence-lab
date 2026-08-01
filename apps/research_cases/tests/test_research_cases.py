from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.market_data.models import Kline
from apps.market_monitoring.models import MarketAnomalyFinding, MarketScanRun
from apps.research_cases.models import ResearchCase
from apps.research_cases.services import get_or_create_case_from_finding


EVENT_TIME = datetime(2024, 3, 8, tzinfo=UTC)


def make_run(**overrides):
    values = {
        "exchange": MarketScanRun.Exchange.BINANCE,
        "market_type": MarketScanRun.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": MarketScanRun.Interval.ONE_DAY,
        "range_start": EVENT_TIME,
        "range_end": EVENT_TIME + timedelta(days=1),
        "trigger": MarketScanRun.Trigger.MANUAL,
        "rules_version": "v1",
        "rules_snapshot": {"version": "v1"},
        "status": MarketScanRun.Status.SUCCESS,
        "expected_count": 1,
        "actual_count": 1,
        "evaluated_count": 1,
        "anomaly_day_count": 1,
        "signal_count": 2,
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
    }
    values.update(overrides)
    return MarketScanRun.objects.create(**values)


def make_kline():
    return Kline.objects.create(
        exchange=Kline.Exchange.BINANCE,
        market_type=Kline.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval=Kline.Interval.ONE_DAY,
        open_time=EVENT_TIME,
        close_time=EVENT_TIME + timedelta(days=1) - timedelta(milliseconds=1),
        open=Decimal("100"),
        high=Decimal("120"),
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("250"),
        quote_volume=Decimal("26250"),
        trade_count=100,
        taker_buy_base_volume=Decimal("125"),
        taker_buy_quote_volume=Decimal("13125"),
    )


def make_finding(run, kline):
    return MarketAnomalyFinding.objects.create(
        run=run,
        kline=kline,
        open_time=EVENT_TIME,
        open=Decimal("100"),
        high=Decimal("120"),
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("250"),
        price_change_pct=Decimal("5"),
        amplitude_pct=Decimal("21"),
        volume_average_20=Decimal("100"),
        volume_ratio=Decimal("2.5"),
        upper_wick_body_ratio=Decimal("3"),
        upper_wick_range_ratio=Decimal("0.714285714285714286"),
        lower_wick_body_ratio=Decimal("0.2"),
        lower_wick_range_ratio=Decimal("0.047619047619047619"),
        signals=[
            {
                "type": "abnormal_change_up",
                "direction": "up",
                "metric": {"name": "price_change_pct", "value": "5", "unit": "percent"},
                "threshold": {"operator": ">=", "value": "5", "unit": "percent"},
            },
            {
                "type": "long_upper_wick",
                "direction": "upper",
                "metric": {
                    "upper_wick_body_ratio": "3",
                    "upper_wick_range_ratio": "0.714285714285714286",
                },
                "threshold": {
                    "body_ratio_value": "3",
                    "range_ratio_value": "0.40",
                },
            },
        ],
    )


class ResearchCaseCreationTests(TestCase):
    def setUp(self):
        self.kline = make_kline()
        self.run = make_run()
        self.finding = make_finding(self.run, self.kline)
        self.create_url = reverse(
            "research_cases:create_from_finding",
            args=[self.finding.pk],
        )

    def test_post_creates_case_and_redirects_to_detail(self):
        response = self.client.post(self.create_url)

        research_case = ResearchCase.objects.get()
        self.assertRedirects(
            response,
            reverse("research_cases:detail", args=[research_case.pk]),
        )
        self.assertEqual(research_case.source_finding, self.finding)
        self.assertEqual(research_case.event_time, EVENT_TIME)
        self.assertEqual(research_case.symbol, "ETHUSDT")

    def test_creation_saves_market_and_signal_snapshots(self):
        self.client.post(self.create_url)
        research_case = ResearchCase.objects.get()

        self.assertEqual(research_case.anomaly_signals_snapshot, self.finding.signals)
        self.assertEqual(research_case.open, Decimal("100"))
        self.assertEqual(research_case.high, Decimal("120"))
        self.assertEqual(research_case.low, Decimal("99"))
        self.assertEqual(research_case.close, Decimal("105"))
        self.assertEqual(research_case.volume, Decimal("250"))
        self.assertEqual(research_case.price_change_pct, Decimal("5"))
        self.assertEqual(research_case.amplitude_pct, Decimal("21"))
        self.assertEqual(research_case.calculation_snapshot["volume_ratio"], "2.5")

        self.finding.signals = [{"type": "abnormal_change_down"}]
        self.finding.close = Decimal("80")
        self.finding.save(update_fields=["signals", "close"])
        research_case.refresh_from_db()
        self.assertEqual(research_case.anomaly_signals_snapshot[0]["type"], "abnormal_change_up")
        self.assertEqual(research_case.close, Decimal("105"))

    def test_duplicate_post_for_same_finding_returns_existing_case(self):
        first_response = self.client.post(self.create_url)
        second_response = self.client.post(self.create_url)

        self.assertEqual(ResearchCase.objects.count(), 1)
        self.assertEqual(first_response.url, second_response.url)

    def test_finding_from_another_run_same_market_day_returns_existing_case(self):
        first_case, created = get_or_create_case_from_finding(self.finding)
        self.assertTrue(created)
        second_run = make_run(started_at=timezone.now() + timedelta(seconds=1))
        second_finding = make_finding(second_run, self.kline)

        response = self.client.post(
            reverse("research_cases:create_from_finding", args=[second_finding.pk])
        )

        self.assertEqual(ResearchCase.objects.count(), 1)
        self.assertRedirects(
            response,
            reverse("research_cases:detail", args=[first_case.pk]),
        )
        first_case.refresh_from_db()
        self.assertEqual(first_case.source_finding, self.finding)

    def test_database_constraint_rejects_duplicate_market_event(self):
        first_case, _ = get_or_create_case_from_finding(self.finding)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ResearchCase.objects.create(
                source_finding=self.finding,
                exchange=first_case.exchange,
                market_type=first_case.market_type,
                symbol=first_case.symbol,
                interval=first_case.interval,
                event_time=first_case.event_time,
                title="重复案例",
                anomaly_signals_snapshot=[],
                calculation_snapshot={},
                open=first_case.open,
                high=first_case.high,
                low=first_case.low,
                close=first_case.close,
                volume=first_case.volume,
                price_change_pct=first_case.price_change_pct,
                amplitude_pct=first_case.amplitude_pct,
            )

    def test_source_finding_uses_protect(self):
        get_or_create_case_from_finding(self.finding)

        with self.assertRaises(ProtectedError):
            self.finding.delete()

    def test_creation_endpoint_rejects_get(self):
        response = self.client.get(self.create_url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(ResearchCase.objects.count(), 0)

    def test_creation_endpoint_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(self.create_url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ResearchCase.objects.count(), 0)


class ResearchCasePageTests(TestCase):
    def setUp(self):
        self.kline = make_kline()
        self.run = make_run()
        self.finding = make_finding(self.run, self.kline)

    def test_list_and_detail_pages_are_accessible(self):
        research_case, _ = get_or_create_case_from_finding(self.finding)

        list_response = self.client.get(reverse("research_cases:list"))
        detail_response = self.client.get(
            reverse("research_cases:detail", args=[research_case.pk])
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertTemplateUsed(list_response, "research_cases/list.html")
        self.assertEqual(detail_response.status_code, 200)
        self.assertTemplateUsed(detail_response, "research_cases/detail.html")

    def test_pages_show_parallel_chinese_signal_labels_and_case_content(self):
        research_case, _ = get_or_create_case_from_finding(self.finding)

        list_response = self.client.get(reverse("research_cases:list"))
        detail_response = self.client.get(
            reverse("research_cases:detail", args=[research_case.pk])
        )

        for response in (list_response, detail_response):
            self.assertContains(response, "大幅上涨")
            self.assertContains(response, "长上影线")
            self.assertContains(response, "ETHUSDT")
        self.assertContains(list_response, f"巡检 #{self.run.pk}")
        self.assertContains(detail_response, "来源与审计")
        self.assertContains(detail_response, "abnormal_change_up")
        self.assertContains(
            detail_response,
            "&quot;type&quot;: &quot;long_upper_wick&quot;",
        )
        for section in ("价格证据", "衍生品证据", "新闻证据", "历史类似", "AI报告"):
            self.assertContains(detail_response, section)

    def test_market_inspection_switches_from_create_to_view_action(self):
        inspection_url = f"{reverse('market_monitoring:index')}?run={self.run.pk}"

        before = self.client.get(inspection_url)
        self.assertContains(before, "建立研究案例")
        self.assertNotContains(before, "查看研究案例")

        research_case, _ = get_or_create_case_from_finding(self.finding)
        after = self.client.get(inspection_url)
        self.assertContains(after, "查看研究案例")
        self.assertContains(
            after,
            reverse("research_cases:detail", args=[research_case.pk]),
        )

    def test_other_scan_finding_same_market_day_shows_existing_case(self):
        research_case, _ = get_or_create_case_from_finding(self.finding)
        second_run = make_run(started_at=timezone.now() + timedelta(seconds=1))
        make_finding(second_run, self.kline)

        response = self.client.get(
            f"{reverse('market_monitoring:index')}?run={second_run.pk}"
        )

        self.assertContains(response, "查看研究案例")
        self.assertContains(
            response,
            reverse("research_cases:detail", args=[research_case.pk]),
        )

    def test_sidebar_maps_case_list_and_detail_to_research_group(self):
        research_case, _ = get_or_create_case_from_finding(self.finding)

        for url in (
            reverse("research_cases:list"),
            reverse("research_cases:detail", args=[research_case.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(
                    response,
                    '<details class="nav-group is-active" data-nav-group="research-cases" open>',
                )
                self.assertContains(
                    response,
                    '<a class="nav-subitem is-active" href="/research-cases/" aria-current="page">案例列表</a>',
                )
