from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.inspection.models import KlineInspectionRun, empty_inspection_details


def make_run(**overrides):
    values = {
        "exchange": KlineInspectionRun.Exchange.BINANCE,
        "market_type": KlineInspectionRun.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": "1h",
        "range_start": datetime(2024, 1, 1, tzinfo=UTC),
        "range_end": datetime(2024, 1, 2, tzinfo=UTC),
        "trigger": KlineInspectionRun.Trigger.MANUAL,
        "status": KlineInspectionRun.Status.SUCCESS,
        "quality_status": KlineInspectionRun.QualityStatus.PASSED,
        "expected_count": 24,
        "actual_count": 24,
        "details": empty_inspection_details(),
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
    }
    values.update(overrides)
    return KlineInspectionRun.objects.create(**values)


class InspectionPageTests(TestCase):
    url = "/inspection/"
    valid_data = {
        "start_date": "2024-01-01",
        "end_date": "2024-01-03",
        "intervals": ["1d", "1h"],
    }

    def test_get_returns_200_and_correct_template(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inspection/index.html")
        self.assertContains(response, "结束日期不包含")

    @patch("apps.inspection.views.inspect_klines")
    def test_valid_post_inspects_each_selected_interval(self, inspect):
        inspect.side_effect = [
            SimpleNamespace(
                status=KlineInspectionRun.Status.SUCCESS,
                quality_status=KlineInspectionRun.QualityStatus.PASSED,
            ),
            SimpleNamespace(
                status=KlineInspectionRun.Status.SUCCESS,
                quality_status=KlineInspectionRun.QualityStatus.PASSED,
            ),
        ]

        response = self.client.post(self.url, self.valid_data)

        self.assertRedirects(response, self.url)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(inspect.call_args_list[0].args[:2], ("ETHUSDT", "1d"))
        self.assertEqual(inspect.call_args_list[1].args[:2], ("ETHUSDT", "1h"))

    @patch("apps.inspection.views.inspect_klines")
    def test_no_interval_reports_form_error(self, inspect):
        response = self.client.post(self.url, {**self.valid_data, "intervals": []})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请至少选择一个检查周期")
        inspect.assert_not_called()

    @patch("apps.inspection.views.inspect_klines")
    def test_start_not_before_end_reports_form_error(self, inspect):
        response = self.client.post(
            self.url,
            {**self.valid_data, "start_date": "2024-01-03"},
        )

        self.assertContains(response, "开始日期必须早于结束日期")
        inspect.assert_not_called()

    @patch("apps.inspection.views.inspect_klines")
    def test_range_over_366_days_reports_form_error(self, inspect):
        response = self.client.post(
            self.url,
            {
                **self.valid_data,
                "start_date": "2023-01-01",
                "end_date": "2024-01-03",
            },
        )

        self.assertContains(response, "单次数据质量检查范围最长为 366 天")
        inspect.assert_not_called()

    @patch("apps.inspection.views.inspect_klines")
    def test_end_after_current_utc_date_reports_form_error(self, inspect):
        tomorrow = timezone.now().date() + timedelta(days=1)
        response = self.client.post(
            self.url,
            {
                **self.valid_data,
                "start_date": timezone.now().date().isoformat(),
                "end_date": tomorrow.isoformat(),
            },
        )

        self.assertContains(response, "结束日期不得超过当前 UTC 日期 00:00")
        inspect.assert_not_called()

    @patch("apps.inspection.views.inspect_klines")
    def test_one_interval_failure_does_not_stop_the_next(self, inspect):
        inspect.side_effect = [
            SimpleNamespace(
                status=KlineInspectionRun.Status.FAILED,
                quality_status=KlineInspectionRun.QualityStatus.PENDING,
            ),
            SimpleNamespace(
                status=KlineInspectionRun.Status.SUCCESS,
                quality_status=KlineInspectionRun.QualityStatus.PASSED,
            ),
        ]

        response = self.client.post(self.url, self.valid_data, follow=True)

        self.assertEqual(inspect.call_count, 2)
        self.assertContains(response, "部分周期数据质量检查执行失败")

    def test_page_displays_only_20_most_recent_runs(self):
        for index in range(21):
            make_run(
                started_at=timezone.now() + timedelta(seconds=index),
                error_message=f"inspection error {index}",
            )

        response = self.client.get(self.url)

        self.assertEqual(len(response.context["recent_runs"]), 20)
        self.assertContains(response, "inspection error 20")
        self.assertNotContains(response, "inspection error 0")

    def test_page_displays_selected_run_issue_details(self):
        details = empty_inspection_details()
        details["missing_ranges"] = [
            {
                "start": "2024-01-01T01:00:00+00:00",
                "end": "2024-01-01T03:00:00+00:00",
                "count": 2,
            }
        ]
        details["invalid_rows"] = [
            {
                "open_time": "2024-01-01T04:00:00+00:00",
                "rules": ["volume_negative"],
            }
        ]
        run = make_run(
            quality_status=KlineInspectionRun.QualityStatus.ISSUES,
            missing_count=2,
            invalid_numeric_count=1,
            details=details,
        )

        response = self.client.get(f"{self.url}?run={run.id}")

        self.assertEqual(response.context["selected_run"], run)
        self.assertContains(response, "缺失时间区间")
        self.assertContains(response, "volume_negative")

    def test_page_explicitly_reports_selected_passed_run_has_no_issues(self):
        run = make_run()

        response = self.client.get(f"{self.url}?run={run.id}")

        self.assertContains(response, "本次范围未发现质量问题")

    def test_csrf_protection_is_enabled(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 403)

    def test_navigation_matches_value_order_and_only_links_built_features(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'href="/inspection/"')
        self.assertContains(response, 'href="/collection/"')
        self.assertContains(response, "inspection/js/form.js")
        self.assertContains(response, "市场异常巡检")
        self.assertNotContains(response, 'href="/market-anomaly/"')
        self.assertNotContains(response, ">人工反馈<")

        html = response.content.decode()
        menu_labels = (
            "总览",
            "AI 报告",
            "研究案例",
            "分析",
            "市场异常巡检",
            "数据质量检查",
            "采集",
            "系统管理",
        )
        positions = [html.index(f">{label}<") for label in menu_labels]
        self.assertEqual(positions, sorted(positions))

    def test_other_unbuilt_routes_remain_unavailable(self):
        for path in (
            "/analysis/",
            "/research/",
            "/reports/",
            "/feedback/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_home_page_has_real_inspection_entry(self):
        response = self.client.get(reverse("core:home"))

        self.assertContains(response, "进入数据质量检查")
        self.assertContains(response, 'href="/inspection/"')
