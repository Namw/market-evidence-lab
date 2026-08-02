from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import call, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.market_data.models import Kline


def make_kline(interval="1h", open_time=None):
    open_time = open_time or datetime(2024, 1, 1, tzinfo=UTC)
    return Kline.objects.create(
        exchange=Kline.Exchange.BINANCE,
        market_type=Kline.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval=interval,
        open_time=open_time,
        close_time=open_time + timedelta(hours=1, milliseconds=-1),
        open=Decimal("1000"),
        high=Decimal("1100"),
        low=Decimal("900"),
        close=Decimal("1050"),
        volume=Decimal("10"),
        quote_volume=Decimal("10000"),
        trade_count=10,
        taker_buy_base_volume=Decimal("5"),
        taker_buy_quote_volume=Decimal("5000"),
    )


def make_run(**overrides):
    values = {
        "exchange": CollectionRun.Exchange.BINANCE,
        "market_type": CollectionRun.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": "1h",
        "range_start": datetime(2024, 1, 1, tzinfo=UTC),
        "range_end": datetime(2024, 1, 2, tzinfo=UTC),
        "trigger": CollectionRun.Trigger.MANUAL,
        "status": CollectionRun.Status.SUCCESS,
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
    }
    values.update(overrides)
    return CollectionRun.objects.create(**values)


def pipeline_result(collection_status=CollectionRun.Status.SUCCESS, quality_status="passed"):
    return SimpleNamespace(
        collection_run=SimpleNamespace(status=collection_status),
        inspection_run=SimpleNamespace(status="success", quality_status=quality_status),
    )


class CollectionPageTests(TestCase):
    url = "/collection/"
    valid_data = {
        "start_date": "2024-01-01",
        "end_date": "2024-01-03",
        "intervals": ["1d", "1h"],
    }

    def test_get_returns_200_and_correct_template(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "collection/index.html")
        self.assertContains(response, "结束日期不包含")

    @patch("apps.collection.views.collect_and_inspect")
    def test_valid_post_collects_and_inspects_each_selected_interval(self, collect):
        collect.side_effect = [pipeline_result(), pipeline_result()]

        response = self.client.post(self.url, self.valid_data)

        self.assertRedirects(response, self.url)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(collect.call_args_list[0].kwargs["data_type"], "kline")
        self.assertEqual(collect.call_args_list[0].kwargs["interval"], "1d")
        self.assertEqual(collect.call_args_list[1].kwargs["interval"], "1h")

    @patch("apps.collection.views.collect_and_inspect")
    def test_1d_failure_does_not_prevent_1h_collection(self, collect):
        collect.side_effect = [
            pipeline_result(CollectionRun.Status.FAILED),
            pipeline_result(),
        ]

        response = self.client.post(self.url, self.valid_data, follow=True)

        self.assertEqual(collect.call_count, 2)
        self.assertContains(response, "采集部分完成")

    @patch("apps.collection.views.collect_and_inspect")
    def test_no_interval_reports_form_error(self, collect):
        data = {**self.valid_data, "intervals": []}

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请至少选择一个采集周期")
        collect.assert_not_called()

    @patch("apps.collection.views.collect_and_inspect")
    def test_start_not_before_end_reports_form_error(self, collect):
        data = {**self.valid_data, "start_date": "2024-01-03"}

        response = self.client.post(self.url, data)

        self.assertContains(response, "开始日期必须早于结束日期")
        collect.assert_not_called()

    @patch("apps.collection.views.collect_and_inspect")
    def test_range_over_366_days_reports_form_error(self, collect):
        data = {
            **self.valid_data,
            "start_date": "2023-01-01",
            "end_date": "2024-01-03",
        }

        response = self.client.post(self.url, data)

        self.assertContains(response, "单次采集范围最长为 366 天")
        collect.assert_not_called()

    @patch("apps.collection.views.collect_and_inspect")
    def test_future_start_date_reports_form_error(self, collect):
        future = timezone.now().date() + timedelta(days=1)
        data = {
            **self.valid_data,
            "start_date": future.isoformat(),
            "end_date": (future + timedelta(days=1)).isoformat(),
        }

        response = self.client.post(self.url, data)

        self.assertContains(response, "开始日期不能是未来日期")
        collect.assert_not_called()

    @patch("apps.collection.views.collect_and_inspect")
    def test_end_after_current_utc_date_reports_form_error(self, collect):
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
        collect.assert_not_called()

    def test_page_displays_data_overview_for_each_interval(self):
        make_kline("1d", datetime(2024, 1, 1, tzinfo=UTC))
        make_kline("1h", datetime(2024, 1, 2, tzinfo=UTC))
        make_kline("1h", datetime(2024, 1, 2, 1, tzinfo=UTC))

        response = self.client.get(self.url)

        overview = response.context["data_overview"]
        self.assertEqual(overview[0]["record_count"], 1)
        self.assertEqual(overview[1]["record_count"], 2)
        self.assertContains(response, "最早 open_time")
        self.assertContains(response, "最新 open_time")

    def test_page_displays_at_most_20_recent_runs_and_error_summary(self):
        for index in range(21):
            make_run(
                started_at=timezone.now() + timedelta(seconds=index),
                error_message=f"readable error {index}",
            )

        response = self.client.get(self.url)

        self.assertEqual(len(response.context["recent_runs"]), 20)
        self.assertContains(response, "readable error 20")
        self.assertNotContains(response, "readable error 0")

    def test_csrf_protection_remains_enabled(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 403)

    def test_unbuilt_business_routes_remain_unavailable(self):
        for path in (
            "/analysis/",
            "/research/",
            "/reports/",
            "/feedback/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_navigation_links_to_unified_data_view_but_not_unbuilt_routes(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'href="/market-data/"')
        self.assertContains(response, 'href="/collection/derivatives/"')
        self.assertContains(response, "行情数据观察")
        self.assertContains(response, "数据查看")
        self.assertContains(
            response,
            '<details class="nav-group is-active" data-nav-group="market-data" open>',
        )
        self.assertContains(
            response,
            '<a class="nav-subitem" href="/market-data/">数据查看</a>',
        )
        self.assertNotContains(response, 'href="/analysis/"')
        self.assertContains(response, "form.js")

    def test_home_page_has_real_collection_entry(self):
        response = self.client.get(reverse("core:home"))

        self.assertContains(response, "进入采集层")
        self.assertContains(response, 'href="/collection/"')
