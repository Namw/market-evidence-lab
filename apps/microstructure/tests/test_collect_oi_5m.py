from datetime import UTC, datetime
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from apps.market_data.models import OpenInterest
from apps.microstructure.management.commands.collect_oi_5m import Command
from apps.microstructure.models import MicrostructureCollectorRun


class CollectOi5mCommandTests(TestCase):
    def test_collect_once_saves_just_finished_period(self):
        run = MicrostructureCollectorRun.objects.create(symbol="ETHUSDT")
        client = Mock()
        payload = Mock(
            timestamp=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
            sum_open_interest=1234,
            sum_open_interest_value=5678,
        )
        client.iter_batches.return_value = [[payload]]

        command = Command()
        with patch(
            "apps.microstructure.management.commands.collect_oi_5m.floor_time",
            return_value=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
        ), patch(
            "apps.microstructure.management.commands.collect_oi_5m.timezone"
        ) as tz, patch(
            "apps.microstructure.management.commands.collect_oi_5m._save_oi_batch",
            return_value=(1, 0, 0),
        ) as save:
            tz.now.return_value = datetime(2026, 8, 17, 1, 2, tzinfo=UTC)
            result = command._collect_once(client, "ETHUSDT", run.pk)

        self.assertTrue(result)
        client.iter_batches.assert_called_once_with(
            symbol="ETHUSDT",
            period=OpenInterest.Period.FIVE_MINUTES,
            range_start=datetime(2026, 8, 17, 0, 55, tzinfo=UTC),
            range_end=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
        )
        save.assert_called_once_with(
            symbol="ETHUSDT",
            period=OpenInterest.Period.FIVE_MINUTES,
            payloads=[payload],
        )
        run.refresh_from_db()
        self.assertIsNotNone(run.heartbeat_at)
        self.assertEqual(run.error_message, "")

    def test_collect_once_records_failure_heartbeat(self):
        run = MicrostructureCollectorRun.objects.create(symbol="ETHUSDT")
        client = Mock()
        client.iter_batches.side_effect = RuntimeError("boom")

        command = Command()
        with patch(
            "apps.microstructure.management.commands.collect_oi_5m.floor_time",
            return_value=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
        ), patch(
            "apps.microstructure.management.commands.collect_oi_5m.timezone"
        ) as tz:
            tz.now.return_value = datetime(2026, 8, 17, 1, 2, tzinfo=UTC)
            result = command._collect_once(client, "ETHUSDT", run.pk)

        self.assertFalse(result)
        run.refresh_from_db()
        self.assertIsNotNone(run.heartbeat_at)
        self.assertIn("OI 采集失败", run.error_message)
