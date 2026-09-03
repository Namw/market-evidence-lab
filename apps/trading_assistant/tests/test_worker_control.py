import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.db import OperationalError
from django.db.models.functions import Now
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from apps.trading_assistant.worker_control import WorkerStartError, repair_stale_worker_lock, start_worker
from apps.trading_assistant.models import WorkerHeartbeat
from apps.trading_assistant.services import worker_online


class WorkerControlTests(SimpleTestCase):
    def setUp(self):
        repair = patch("apps.trading_assistant.worker_control.repair_stale_worker_lock", return_value=False)
        self.repair = repair.start()
        self.addCleanup(repair.stop)
        wait = patch("apps.trading_assistant.worker_control.STARTUP_WAIT_SECONDS", 0.3)
        wait.start()
        self.addCleanup(wait.stop)

    @patch("apps.trading_assistant.worker_control.worker_online", return_value=True)
    @patch("apps.trading_assistant.worker_control.subprocess.Popen")
    def test_online_worker_is_not_restarted(self, launch, online):
        self.assertTrue(start_worker()["worker_online"])
        launch.assert_not_called()
        self.repair.assert_not_called()

    @patch("apps.trading_assistant.worker_control.worker_online", return_value=False)
    def test_child_holds_launch_lock_and_exit_allows_restart(self, online):
        # Real subprocess/descriptor test, isolated from the actual application
        # and database. No production worker or pending analysis is started.
        children = []
        popen = subprocess.Popen

        def launch(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "manage.py").write_text("import time\ntime.sleep(60)\n")
            with override_settings(BASE_DIR=base), patch("apps.trading_assistant.worker_control.subprocess.Popen", side_effect=launch):
                try:
                    self.assertTrue(start_worker()["starting"])
                    self.assertTrue(start_worker()["starting"])
                    self.assertEqual(len(children), 1)
                    self.assertIsNone(children[0].poll())
                    children[0].terminate()
                    children[0].wait(timeout=5)
                    self.assertTrue(start_worker()["starting"])
                    self.assertEqual(len(children), 2)
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.terminate()
                        child.wait(timeout=5)

    @patch("apps.trading_assistant.worker_control.worker_online", return_value=False)
    def test_immediate_exit_is_reported(self, online):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            (Path(directory) / "manage.py").write_text("raise SystemExit(1)\n")
            with self.assertRaises(RuntimeError):
                start_worker()

    @patch("apps.trading_assistant.views.start_worker")
    @patch("apps.trading_assistant.views.worker_online", return_value=False)
    def test_read_only_status_and_csrf_protected_start(self, online, launch):
        url = reverse("trading_assistant:worker")
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(url).json(), {"worker_online": False})
        self.assertEqual(client.post(url).status_code, 403)
        launch.assert_not_called()
        client.get(reverse("trading_assistant:index"))
        launch.return_value = {"worker_online": False, "starting": True}
        response = client.post(url, data={}, content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["starting"])
        launch.assert_called_once_with()

    @patch("apps.trading_assistant.views.start_worker", side_effect=OperationalError("private database details"))
    def test_database_failure_has_actionable_safe_error(self, launch):
        response = self.client.post(reverse("trading_assistant:worker"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("迁移", response.json()["error"])
        self.assertNotIn("private", response.content.decode())

    @patch("apps.trading_assistant.views.start_worker", side_effect=WorkerStartError("已有分析进程正在工作，请稍后重试。"))
    def test_recovery_reason_is_shown_instead_of_generic_failure(self, launch):
        response = self.client.post(reverse("trading_assistant:worker"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "已有分析进程正在工作，请稍后重试。")

    @patch("apps.trading_assistant.worker_control.worker_online", return_value=False)
    @patch("apps.trading_assistant.worker_control.subprocess.Popen")
    def test_unsafe_recovery_never_launches_a_second_worker(self, launch, online):
        self.repair.side_effect = WorkerStartError("旧连接仍在工作")
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            with self.assertRaisesMessage(WorkerStartError, "旧连接仍在工作"):
                start_worker()
        launch.assert_not_called()


class HeartbeatClockTests(TestCase):
    def test_local_clock_skew_does_not_change_online_status(self):
        WorkerHeartbeat.objects.create(name="default", seen_at=Now())
        for hours in (-8, 8):
            with patch("apps.trading_assistant.services.timezone.now", return_value=timezone.now() + timedelta(hours=hours)):
                self.assertTrue(worker_online())
        WorkerHeartbeat.objects.update(seen_at=Now() - timedelta(minutes=10))
        self.assertFalse(worker_online())
        WorkerHeartbeat.objects.update(seen_at=Now() + timedelta(hours=8))
        self.assertFalse(worker_online())


class RecoveryTests(SimpleTestCase):
    def setUp(self):
        params = patch("apps.trading_assistant.worker_control.worker_connection_params", return_value={})
        params.start()
        self.addCleanup(params.stop)
        connect = patch("apps.trading_assistant.worker_control.psycopg.connect")
        self.conn = connect.start().return_value.__enter__.return_value
        self.addCleanup(connect.stop)

    def results(self, *rows):
        from unittest.mock import Mock
        self.conn.execute.side_effect = [Mock(**{"fetchone.return_value": row}) for row in rows]

    def test_no_holder_needs_no_termination(self):
        self.results({"locked": True}, None)
        self.assertFalse(repair_stale_worker_lock())
        self.assertEqual(self.conn.execute.call_count, 2)

    def test_active_or_recent_holder_is_never_terminated(self):
        self.results({"locked": True}, {"pid": 12, "recoverable": False})
        with self.assertRaisesMessage(WorkerStartError, "保护"):
            repair_stale_worker_lock()
        self.assertEqual(self.conn.execute.call_count, 2)

    def test_stale_holder_identity_is_rechecked_before_termination(self):
        self.results({"locked": True}, {"pid": 12, "recoverable": True, "backend_start": "start", "state_change": "idle"}, {"terminated": True}, None)
        self.assertTrue(repair_stale_worker_lock())
        sql, args = self.conn.execute.call_args_list[2].args
        self.assertIn("backend_start = %s AND state_change = %s", sql)
        self.assertEqual(args[-3:], (12, "start", "idle"))

    def test_recovered_heartbeat_or_changed_activity_cancels_termination(self):
        self.results({"locked": True}, {"pid": 12, "recoverable": True, "backend_start": "start", "state_change": "idle"}, None)
        with self.assertRaisesMessage(WorkerStartError, "状态已变化"):
            repair_stale_worker_lock()

    def test_other_recovery_request_is_not_interrupted(self):
        self.results({"locked": False})
        with self.assertRaisesMessage(WorkerStartError, "另一个启动请求"):
            repair_stale_worker_lock()
        self.assertEqual(self.conn.execute.call_count, 1)
