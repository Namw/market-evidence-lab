from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.market_data.models import OpenInterest
from apps.microstructure.management.commands.collect_orderbook import Command
from apps.microstructure.models import MarketMinute, MicrostructureCollectorRun


class MicrostructureViewTests(TestCase):
    def test_page_is_available_and_navigation_has_entry(self):
        response = self.client.get(reverse("microstructure:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "主动成交与 Delta")
        self.assertContains(response, "盘口深度与价差")
        self.assertContains(response, "启动采集")
        self.assertContains(response, 'href="/microstructure/"')
        self.assertContains(response, 'href="/microstructure/research/"')

    def test_research_page_shows_all_metrics_in_one_comparison(self):
        start = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
        for offset in range(20):
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start + timedelta(minutes=offset),
                minute_end=start + timedelta(minutes=offset + 1),
                close_price="3200",
                taker_buy_quote=str(100 + offset),
                taker_sell_quote=str(120 - offset),
                future_5m_return=str((offset - 10) / 10000),
                kline_closed=True,
            )
        response = self.client.get(reverse("microstructure:research"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "异常候选联合研究")
        self.assertContains(response, "六个指标，放在一起比较")
        self.assertContains(response, "主动成交失衡")
        self.assertContains(response, "成交强度")
        self.assertContains(response, "盘口深度减少")
        self.assertContains(response, "Spread 扩大")
        self.assertContains(response, "Top5盘口失衡")
        self.assertContains(response, "成交-价格背离")
        self.assertContains(response, "平均未来5分钟收益")
        self.assertContains(response, "精确数据与分组边界")
        self.assertContains(response, "当前仍不预设异常阈值")
        chart_labels = [
            item["return_chart"]["maximum_label"]
            for item in response.context["research_items"]
        ]
        self.assertEqual(len(set(chart_labels)), 1)

    def test_research_page_shows_spread_expansion_definition(self):
        response = self.client.get(reverse("microstructure:research"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "当前1分钟 spread_bps_p95 / 前60个连续有效分钟 spread_bps_p95 中位数",
        )
        self.assertContains(response, "相对收窄 → 相对扩大")

    def test_research_page_shows_top5_imbalance_definition(self):
        response = self.client.get(reverse("microstructure:research"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "(Top5买盘金额 − Top5卖盘金额) / (Top5买盘金额 + Top5卖盘金额)",
        )
        self.assertContains(response, "近端卖盘厚 → 近端买盘厚")

    def test_research_page_shows_flow_price_mismatch_definition(self):
        response = self.client.get(reverse("microstructure:research"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "主动成交失衡 × |close / open − 1|（仅方向相反时）",
        )
        self.assertContains(
            response,
            "主动卖出但价格上涨 → 主动买入但价格下跌",
        )

    def test_research_page_keeps_trade_intensity_definition(self):
        start = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
        for offset in range(80):
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start + timedelta(minutes=offset),
                minute_end=start + timedelta(minutes=offset + 1),
                close_price="3200",
                quote_volume=str(1000 + offset),
                future_5m_return="0.001",
                kline_closed=True,
            )

        response = self.client.get(
            reverse("microstructure:research"),
            {"metric": "trade_intensity"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "当前1分钟成交额 / 前60个连续完整分钟成交额中位数",
        )
        self.assertContains(response, "D1 低成交强度 → D10 高成交强度")

    def test_research_page_keeps_depth_drop_definition(self):
        start = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
        for offset in range(20):
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start + timedelta(minutes=offset),
                minute_end=start + timedelta(minutes=offset + 1),
                close_price="3200",
                future_5m_return="0.001",
                kline_closed=True,
                bid_depth_open="600",
                ask_depth_open="400",
                bid_depth_close=str(600 - offset * 3),
                ask_depth_close=str(400 - offset * 2),
                book_sample_count=60,
                coverage_ratio="1",
            )

        response = self.client.get(
            reverse("microstructure:research"),
            {"metric": "depth_drop"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "(分钟初Top20总深度 − 分钟末Top20总深度) / 分钟初Top20总深度",
        )
        self.assertContains(response, "D1 深度增加 → D10 深度减少")

    def test_status_returns_current_run_progress(self):
        MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            connection_state=MicrostructureCollectorRun.ConnectionState.CONNECTED,
            received_messages=42,
            saved_minute_updates=20,
            heartbeat_at=timezone.now(),
        )

        response = self.client.get(reverse("microstructure:status"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["run"]["status"], "running")
        self.assertEqual(payload["run"]["received_messages"], 42)
        self.assertTrue(payload["can_stop"])
        self.assertFalse(payload["can_start"])

    def test_status_returns_display_ready_minutes_in_time_order(self):
        later = datetime(2026, 8, 17, 1, 1, tzinfo=UTC)
        earlier = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
        for start, close in ((later, "3201"), (earlier, "3200")):
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start,
                minute_end=start + timedelta(minutes=1),
                open_price="3199",
                high_price="3202",
                low_price="3198",
                close_price=close,
                quote_volume="1000000",
                taker_buy_quote="600000",
                taker_sell_quote="400000",
                delta_quote="200000",
            )

        minutes = self.client.get(reverse("microstructure:status")).json()["minutes"]

        self.assertEqual([row["close"] for row in minutes], ["3200.000000000000000000", "3201.000000000000000000"])
        self.assertIn("future_5m_return", minutes[0])

    def test_status_can_switch_between_collector_runs(self):
        first_start = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
        second_start = datetime(2026, 8, 17, 5, 0, tzinfo=UTC)
        first_run = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.STOPPED,
            started_at=first_start,
            stopped_at=first_start + timedelta(hours=2),
            saved_minute_updates=120,
        )
        second_run = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            started_at=second_start,
            saved_minute_updates=2,
        )
        for start, close in ((first_start, "3100"), (second_start, "3200")):
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start,
                minute_end=start + timedelta(minutes=1),
                open_price=close,
                high_price=close,
                low_price=close,
                close_price=close,
            )

        latest = self.client.get(reverse("microstructure:status")).json()
        historical = self.client.get(
            reverse("microstructure:status"),
            {"run_id": first_run.pk},
        ).json()

        self.assertEqual(latest["selected_run_id"], second_run.pk)
        self.assertTrue(latest["selected_run_active"])
        self.assertEqual([row["close"] for row in latest["minutes"]], ["3200.000000000000000000"])
        self.assertEqual(historical["selected_run_id"], first_run.pk)
        self.assertFalse(historical["selected_run_active"])
        self.assertEqual([row["close"] for row in historical["minutes"]], ["3100.000000000000000000"])
        self.assertEqual(
            [item["id"] for item in historical["available_runs"]],
            [second_run.pk, first_run.pk],
        )

    def test_status_paginates_older_minutes_with_before_cursor(self):
        run = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.STOPPED,
            started_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
            stopped_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
            saved_minute_updates=60,
        )
        for minute in range(60):
            start = datetime(2026, 8, 17, 1, 0, tzinfo=UTC) + timedelta(minutes=minute)
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=start,
                minute_end=start + timedelta(minutes=1),
                open_price="3200",
                high_price="3201",
                low_price="3199",
                close_price="3200",
            )

        first = self.client.get(
            reverse("microstructure:status"),
            {"run_id": run.pk, "minutes": 25},
        ).json()
        older = self.client.get(
            reverse("microstructure:status"),
            {
                "run_id": run.pk,
                "minutes": 25,
                "before": first["minutes"][0]["minute_start"],
            },
        ).json()

        self.assertEqual(len(first["minutes"]), 25)
        self.assertTrue(first["has_more"])
        self.assertEqual(
            [row["close"] for row in first["minutes"]],
            ["3200.000000000000000000"] * 25,
        )
        self.assertEqual(len(older["minutes"]), 25)
        self.assertTrue(older["has_more"])
        self.assertLess(
            older["minutes"][-1]["minute_start"],
            first["minutes"][0]["minute_start"],
        )
        self.assertEqual(
            older["oldest_loaded_stamp"],
            older["minutes"][0]["minute_start"],
        )

        tail = self.client.get(
            reverse("microstructure:status"),
            {
                "run_id": run.pk,
                "minutes": 25,
                "before": older["minutes"][0]["minute_start"],
            },
        ).json()
        self.assertEqual(len(tail["minutes"]), 10)
        self.assertFalse(tail["has_more"])

    def test_status_returns_five_minute_open_interest(self):
        for minute in (0, 5, 10):
            timestamp = datetime(2026, 8, 17, 1, minute, tzinfo=UTC)
            OpenInterest.objects.create(
                exchange="binance",
                market_type="usd_m_futures",
                symbol="ETHUSDT",
                period=OpenInterest.Period.FIVE_MINUTES,
                timestamp=timestamp,
                sum_open_interest=f"{1000 + minute}",
                sum_open_interest_value=f"{3_000_000 + minute}",
            )

        payload = self.client.get(reverse("microstructure:status")).json()

        rows = payload["oi_5m"]
        self.assertEqual(
            [row["timestamp"] for row in rows],
            [
                "2026-08-17T01:00:00Z",
                "2026-08-17T01:05:00Z",
                "2026-08-17T01:10:00Z",
            ],
        )
        self.assertEqual(rows[-1]["value"], "1010.000000000000000000")
        self.assertEqual(rows[-1]["value_usdt"], "3000010.000000000000000000")

    def test_stopping_run_cannot_receive_duplicate_stop(self):
        MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.STOPPING,
        )

        payload = self.client.get(reverse("microstructure:status")).json()

        self.assertFalse(payload["can_start"])
        self.assertFalse(payload["can_stop"])

    def test_status_returns_latest_order_book_levels(self):
        MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            latest_event_time=datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC),
            latest_update_id=987,
            latest_bids=[{"price": "4200.10", "quantity": "1.25"}],
            latest_asks=[{"price": "4200.20", "quantity": "0.75"}],
        )

        book = self.client.get(reverse("microstructure:status")).json()[
            "latest_order_book"
        ]

        self.assertEqual(book["update_id"], 987)
        self.assertEqual(book["bids"][0]["price"], "4200.10")
        self.assertEqual(book["asks"][0]["quantity"], "0.75")

    @patch("apps.microstructure.views.launch_collector")
    def test_start_endpoint_launches_collector(self, launch):
        launch.return_value = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT"
        )

        response = self.client.post(reverse("microstructure:start"))

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        launch.assert_called_once_with(symbol="ETHUSDT")

    @patch("apps.microstructure.views.stop_collector")
    def test_stop_endpoint_requests_graceful_stop(self, stop):
        stop.return_value = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.STOPPING,
        )

        response = self.client.post(reverse("microstructure:stop"))

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        stop.assert_called_once_with()

    def test_control_endpoints_keep_csrf_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)

        start_response = csrf_client.post(reverse("microstructure:start"))
        stop_response = csrf_client.post(reverse("microstructure:stop"))

        self.assertEqual(start_response.status_code, 403)
        self.assertEqual(stop_response.status_code, 403)


class CollectorRunProgressTests(TestCase):
    def test_command_progress_writer_updates_run(self):
        run = MicrostructureCollectorRun.objects.create(symbol="ETHUSDT")
        event_time = datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC)
        sampled_at = datetime(2026, 8, 17, 1, 2, 4, tzinfo=UTC)
        collector = SimpleNamespace(
            connection_state="connected",
            received_messages=20,
            saved_minute_updates=10,
            reconnect_count=1,
            latest=SimpleNamespace(
                event_time=event_time,
                update_id=456,
                bids=[SimpleNamespace(price=4200, quantity=1.25)],
                asks=[SimpleNamespace(price=4201, quantity=0.75)],
            ),
            latest_kline=None,
            latest_sampled_at=sampled_at,
            last_error="",
        )

        Command._write_progress(run.pk, collector)

        run.refresh_from_db()
        self.assertEqual(run.connection_state, "connected")
        self.assertEqual(run.received_messages, 20)
        self.assertEqual(run.saved_minute_updates, 10)
        self.assertEqual(run.latest_event_time, event_time)
        self.assertEqual(run.latest_sampled_at, sampled_at)
        self.assertEqual(run.latest_update_id, 456)
        self.assertEqual(
            run.latest_bids,
            [{"price": "4200", "quantity": "1.25"}],
        )
        self.assertEqual(
            run.latest_asks,
            [{"price": "4201", "quantity": "0.75"}],
        )
        self.assertIsNotNone(run.heartbeat_at)
