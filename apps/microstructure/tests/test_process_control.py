import signal
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.microstructure.models import MicrostructureCollectorRun
from apps.microstructure.process_control import (
    CollectorControlError,
    _process_command,
    _unmanaged_collector_exists,
    launch_collector,
    stop_collector,
)


class CollectorProcessControlTests(TestCase):
    @patch("apps.microstructure.process_control.subprocess.run")
    @patch("apps.microstructure.process_control.os.name", "nt")
    def test_windows_process_query_uses_cim(self, run):
        run.return_value = Mock(returncode=0, stdout="python manage.py collect_orderbook")

        command = _process_command(4321)

        self.assertEqual(command, "python manage.py collect_orderbook")
        invoked = run.call_args.args[0]
        self.assertEqual(invoked[0], "powershell.exe")
        self.assertIn("ProcessId = 4321", invoked[-1])

    @patch("apps.microstructure.process_control.subprocess.run")
    @patch("apps.microstructure.process_control.os.name", "nt")
    def test_windows_unmanaged_query_only_lists_python_processes(self, run):
        run.return_value = Mock(
            returncode=0,
            stdout="python manage.py collect_orderbook --symbol ETHUSDT",
        )

        self.assertTrue(_unmanaged_collector_exists())
        self.assertIn("Name = 'python.exe'", run.call_args.args[0][-1])

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
    @patch("apps.microstructure.process_control.os.name", "posix")
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
    @patch("apps.microstructure.process_control.os.name", "nt")
    @patch(
        "apps.microstructure.process_control.process_matches_run",
        return_value=True,
    )
    def test_windows_stop_marks_terminated_run_as_stopped(self, matches, kill):
        run = MicrostructureCollectorRun.objects.create(
            symbol="ETHUSDT",
            status=MicrostructureCollectorRun.Status.RUNNING,
            process_id=9876,
            oi_process_id=6543,
        )

        stopped = stop_collector()

        self.assertEqual(stopped.pk, run.pk)
        self.assertEqual(stopped.status, MicrostructureCollectorRun.Status.STOPPED)
        self.assertEqual(
            stopped.connection_state,
            MicrostructureCollectorRun.ConnectionState.DISCONNECTED,
        )
        self.assertIsNotNone(stopped.stopped_at)

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
