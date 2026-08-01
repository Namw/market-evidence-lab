from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from apps.market_data.models import Kline
from apps.market_monitoring.models import MarketAnomalyFinding, MarketScanRun


def make_run(**overrides):
    values = {
        "exchange": MarketScanRun.Exchange.BINANCE,
        "market_type": MarketScanRun.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": "1d",
        "range_start": datetime(2024, 1, 1, tzinfo=UTC),
        "range_end": datetime(2024, 1, 2, tzinfo=UTC),
        "trigger": MarketScanRun.Trigger.MANUAL,
        "rules_version": "v1",
        "rules_snapshot": {"version": "v1"},
        "status": MarketScanRun.Status.SUCCESS,
        "expected_count": 1,
        "actual_count": 1,
        "evaluated_count": 1,
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
    }
    values.update(overrides)
    return MarketScanRun.objects.create(**values)


def make_finding(run):
    open_time = datetime(2024, 1, 1, tzinfo=UTC)
    kline = Kline.objects.create(
        exchange=Kline.Exchange.BINANCE,
        market_type=Kline.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval="1d",
        open_time=open_time,
        close_time=open_time + timedelta(days=1) - timedelta(milliseconds=1),
        open=Decimal("100"), high=Decimal("106"), low=Decimal("99"), close=Decimal("105"),
        volume=Decimal("200"), quote_volume=Decimal("20000"), trade_count=20,
        taker_buy_base_volume=Decimal("100"), taker_buy_quote_volume=Decimal("10000"),
    )
    return MarketAnomalyFinding.objects.create(
        run=run, kline=kline, open_time=open_time,
        open=kline.open, high=kline.high, low=kline.low, close=kline.close, volume=kline.volume,
        price_change_pct=Decimal("5"), amplitude_pct=Decimal("7"),
        volume_average_20=Decimal("100"), volume_ratio=Decimal("2"),
        upper_wick_body_ratio=Decimal("0.2"), upper_wick_range_ratio=Decimal("0.142857"),
        lower_wick_body_ratio=Decimal("0.2"), lower_wick_range_ratio=Decimal("0.142857"),
        signals=[{
            "type": "abnormal_change_up", "direction": "up",
            "metric": {"name": "price_change_pct", "value": "5", "unit": "percent"},
            "threshold": {"operator": ">=", "value": "5", "unit": "percent"},
        }],
    )


class MarketInspectionPageTests(TestCase):
    url = "/market-inspection/"
    valid_data = {"start_date": "2024-01-01", "end_date": "2024-01-03"}

    def test_get_returns_200(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "market_monitoring/index.html")
        self.assertContains(response, "结束日期不包含")
        self.assertContains(response, "V1 异常规则")

    @patch("apps.market_monitoring.views.scan_market_anomalies")
    def test_valid_post_calls_unified_service_and_uses_prg(self, scan):
        scan.return_value = SimpleNamespace(
            pk=42,
            status=MarketScanRun.Status.SUCCESS,
            anomaly_day_count=0,
        )

        response = self.client.post(self.url, self.valid_data)

        self.assertRedirects(response, "/market-inspection/?run=42", fetch_redirect_response=False)
        scan.assert_called_once()
        self.assertEqual(scan.call_args.args[0], datetime(2024, 1, 1, tzinfo=UTC))
        self.assertEqual(scan.call_args.args[1], datetime(2024, 1, 3, tzinfo=UTC))

    @patch("apps.market_monitoring.views.scan_market_anomalies")
    def test_invalid_date_order_shows_form_error(self, scan):
        response = self.client.post(
            self.url,
            {"start_date": "2024-01-03", "end_date": "2024-01-03"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "开始日期必须早于结束日期")
        scan.assert_not_called()

    @patch("apps.market_monitoring.views.scan_market_anomalies")
    def test_range_over_366_days_shows_form_error(self, scan):
        response = self.client.post(
            self.url,
            {"start_date": "2023-01-01", "end_date": "2024-01-03"},
        )

        self.assertContains(response, "最长为 366 天")
        scan.assert_not_called()

    @patch("apps.market_monitoring.views.scan_market_anomalies")
    def test_unclosed_day_shows_form_error(self, scan):
        tomorrow = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        response = self.client.post(
            self.url,
            {
                "start_date": timezone.now().astimezone(UTC).date().isoformat(),
                "end_date": tomorrow.isoformat(),
            },
        )

        self.assertContains(response, "不得超过当前 UTC 日期 00:00")
        scan.assert_not_called()

    def test_csrf_protection_is_enabled(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 403)

    def test_recent_twenty_runs_are_displayed(self):
        for index in range(21):
            make_run(
                started_at=timezone.now() + timedelta(seconds=index),
                error_message=f"market scan marker {index}",
            )

        response = self.client.get(self.url)

        self.assertEqual(len(response.context["recent_runs"]), 20)
        self.assertContains(response, "market scan marker 20")
        self.assertNotContains(response, "market scan marker 0")

    def test_selected_run_displays_finding_metrics_and_signals(self):
        run = make_run(anomaly_day_count=1, signal_count=1)
        make_finding(run)

        response = self.client.get(f"{self.url}?run={run.pk}")

        self.assertEqual(response.context["selected_run"], run)
        self.assertContains(response, "<th>序号</th>", html=True)
        self.assertContains(response, "<td>1</td>", html=True)
        self.assertContains(response, "<th>异常类型</th>", html=True)
        self.assertContains(response, "大幅上涨")
        self.assertContains(response, "查看计算明细（1 个信号）")
        self.assertContains(response, "abnormal_change_up")
        self.assertContains(response, "name=price_change_pct")
        self.assertContains(response, "value=5")

    def test_multiple_signal_types_are_displayed_as_parallel_chinese_labels(self):
        run = make_run(anomaly_day_count=1, signal_count=2)
        finding = make_finding(run)
        finding.signals.append(
            {
                "type": "long_upper_wick",
                "direction": "upper",
                "metric": {
                    "upper_wick_body_ratio": "3.5",
                    "upper_wick_range_ratio": "0.5",
                },
                "threshold": {
                    "body_ratio_value": "3",
                    "range_ratio_value": "0.40",
                },
            }
        )
        finding.save(update_fields=["signals"])

        response = self.client.get(f"{self.url}?run={run.pk}")

        self.assertContains(response, "大幅上涨")
        self.assertContains(response, "长上影线")
        self.assertContains(response, "查看计算明细（2 个信号）")
        self.assertContains(response, "upper_wick_body_ratio=3.5")

    def test_successful_empty_run_has_explicit_no_anomaly_message(self):
        run = make_run(anomaly_day_count=0)

        response = self.client.get(f"{self.url}?run={run.pk}")

        self.assertContains(response, "本次范围未发现符合V1规则的市场异常")

    def test_incomplete_coverage_has_prominent_warning(self):
        run = make_run(
            missing_count=1,
            skipped_invalid_count=1,
            volume_baseline_unavailable_count=1,
        )

        response = self.client.get(f"{self.url}?run={run.pk}")

        self.assertContains(response, "扫描覆盖不完整")
        self.assertContains(response, "异常结果不代表这些日期已完成全部V1规则判断")

    def test_navigation_uses_value_inspection_group_and_quality_link_is_real(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn(
            '<details class="nav-group is-active" data-nav-group="market-monitoring" open>',
            html,
        )
        self.assertIn(
            '<a class="nav-subitem is-active" href="/market-inspection/" aria-current="page">今日巡检结果</a>',
            html,
        )
        self.assertIn('href="/market-inspection/"', html)
        self.assertIn('href="/inspection/"', html)
        self.assertIn("前往数据质量检查", html)

    def test_unbuilt_features_remain_unavailable_and_disabled(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertNotIn('href="/analysis/"', html)
        self.assertNotIn('href="/research/"', html)
        self.assertNotIn('href="/reports/"', html)
        for path in ("/analysis/", "/research/", "/reports/"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_native_duplicate_submit_protection_script_is_loaded(self):
        response = self.client.get(self.url)

        self.assertContains(response, "market_monitoring/js/form.js")
