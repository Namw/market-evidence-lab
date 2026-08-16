from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.microstructure.management.commands.collect_orderbook import Command
from apps.microstructure.models import MicrostructureCollectorRun


class MicrostructureViewTests(TestCase):
    def test_page_is_available_and_navigation_has_entry(self):
        response = self.client.get(reverse("microstructure:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "盘口采集")
        self.assertContains(response, "Top20 订单簿")
        self.assertContains(response, "启动采集")
        self.assertContains(response, 'href="/microstructure/"')

    def test_status_returns_current_run_progress(self):
        MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            connection_state=MicrostructureCollectorRun.ConnectionState.CONNECTED,
            received_messages=42,
            saved_snapshots=20,
            heartbeat_at=timezone.now(),
        )

        response = self.client.get(reverse("microstructure:status"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["run"]["status"], "running")
        self.assertEqual(payload["run"]["received_messages"], 42)
        self.assertTrue(payload["can_stop"])
        self.assertFalse(payload["can_start"])

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
            saved_snapshots=10,
            reconnect_count=1,
            latest=SimpleNamespace(
                event_time=event_time,
                update_id=456,
                bids=[SimpleNamespace(price=4200, quantity=1.25)],
                asks=[SimpleNamespace(price=4201, quantity=0.75)],
            ),
            latest_sampled_at=sampled_at,
            last_error="",
        )

        Command._write_progress(run.pk, collector)

        run.refresh_from_db()
        self.assertEqual(run.connection_state, "connected")
        self.assertEqual(run.received_messages, 20)
        self.assertEqual(run.saved_snapshots, 10)
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
