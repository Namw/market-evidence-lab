import signal
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.microstructure.models import MicrostructureCollectorRun
from apps.microstructure.process_control import (
    CollectorControlError,
    launch_collector,
    stop_collector,
)


class CollectorProcessControlTests(TestCase):
    @patch("apps.microstructure.process_control.subprocess.Popen")
    @patch(
        "apps.microstructure.process_control._unmanaged_collector_exists",
        return_value=False,
    )
    def test_launch_uses_current_python_and_records_process_id(
        self,
        unmanaged,
        popen,
    ):
        popen.return_value = Mock(pid=4321)

        run = launch_collector(symbol="ETHUSDT")

        self.assertEqual(run.status, MicrostructureCollectorRun.Status.STARTING)
        self.assertEqual(run.process_id, 4321)
        self.assertEqual(run.oi_process_id, 4321)
        commands = [call.args[0] for call in popen.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn("collect_orderbook", commands[0])
        self.assertIn("collect_oi_5m", commands[1])
        self.assertEqual(commands[1][-2:], ["--run-id", str(run.pk)])

    @patch(
        "apps.microstructure.process_control._unmanaged_collector_exists",
        return_value=True,
    )
    def test_launch_refuses_terminal_started_collector(self, unmanaged):
        with self.assertRaisesMessage(CollectorControlError, "终端启动"):
            launch_collector(symbol="ETHUSDT")

        self.assertFalse(MicrostructureCollectorRun.objects.exists())

    @patch("apps.microstructure.process_control.os.kill")
    @patch(
        "apps.microstructure.process_control.process_matches_run",
        return_value=True,
    )
    def test_stop_only_signals_verified_collector(self, matches, kill):
        MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            process_id=9876,
            oi_process_id=6543,
        )

        stopped = stop_collector()

        stopped.refresh_from_db()
        self.assertEqual(stopped.status, MicrostructureCollectorRun.Status.STOPPING)
        kill.assert_any_call(9876, signal.SIGTERM)
        kill.assert_any_call(6543, signal.SIGTERM)

    @patch("apps.microstructure.process_control.os.kill")
    @patch(
        "apps.microstructure.process_control.process_matches_run",
        return_value=False,
    )
    def test_stop_does_not_signal_unverified_process(self, matches, kill):
        run = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            process_id=9876,
        )

        with self.assertRaises(CollectorControlError):
            stop_collector()

        run.refresh_from_db()
        self.assertEqual(run.status, MicrostructureCollectorRun.Status.FAILED)
        kill.assert_not_called()
