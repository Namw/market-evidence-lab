from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from apps.scheduling.models import WorkflowRun, empty_workflow_details
from apps.scheduling.services import get_builtin_schedule


def make_workflow_run(**overrides):
    values = {
        "schedule": None,
        "trigger": WorkflowRun.Trigger.MANUAL,
        "range_start": datetime(2026, 7, 28, tzinfo=UTC),
        "range_end": datetime(2026, 7, 31, tzinfo=UTC),
        "status": WorkflowRun.Status.SUCCESS,
        "quality_status": WorkflowRun.QualityStatus.PASSED,
        "details": empty_workflow_details(),
        "started_at": timezone.now(),
        "finished_at": timezone.now(),
    }
    values.update(overrides)
    return WorkflowRun.objects.create(**values)


class SchedulingPageTests(TestCase):
    url = "/system/schedules/"

    def test_get_returns_200_and_displays_real_executor_state(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "scheduling/index.html")
        self.assertContains(response, "调度执行器离线")
        self.assertContains(response, "ETHUSDT每日K线采集与数据质量检查")

    def test_configuration_form_accepts_valid_values_and_uses_prg(self):
        response = self.client.post(
            self.url,
            {
                "action": "save",
                "enabled": "on",
                "run_time": "09:15",
                "lookback_days": "7",
            },
        )

        self.assertRedirects(response, self.url)
        schedule = get_builtin_schedule()
        self.assertTrue(schedule.enabled)
        self.assertEqual(schedule.run_time.hour, 9)
        self.assertEqual(schedule.lookback_days, 7)
        self.assertEqual(schedule.timezone, "Asia/Shanghai")

    def test_configuration_form_rejects_lookback_outside_1_to_30(self):
        for value in ("0", "31"):
            with self.subTest(value=value):
                response = self.client.post(
                    self.url,
                    {
                        "action": "save",
                        "run_time": "08:05",
                        "lookback_days": value,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("lookback_days", response.context["form"].errors)

    @patch("apps.scheduling.views.execute_workflow")
    def test_immediate_post_calls_unified_workflow_and_redirects_to_detail(self, execute):
        execute.return_value = SimpleNamespace(
            pk=88,
            status=WorkflowRun.Status.SUCCESS,
            quality_status=WorkflowRun.QualityStatus.PASSED,
        )
        get_response = self.client.get(self.url)
        token = get_response.context["run_token"]

        response = self.client.post(
            self.url,
            {"action": "run", "run_token": token},
        )

        self.assertRedirects(response, "/system/schedules/?run=88", fetch_redirect_response=False)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs["trigger"], WorkflowRun.Trigger.MANUAL)
        self.assertIsNone(execute.call_args.kwargs["schedule"])

    @patch("apps.scheduling.views.execute_workflow")
    def test_immediate_request_token_prevents_duplicate_submission(self, execute):
        execute.return_value = SimpleNamespace(
            pk=89,
            status=WorkflowRun.Status.SUCCESS,
            quality_status=WorkflowRun.QualityStatus.PASSED,
        )
        token = self.client.get(self.url).context["run_token"]
        payload = {"action": "run", "run_token": token}

        self.client.post(self.url, payload)
        response = self.client.post(self.url, payload, follow=True)

        self.assertEqual(execute.call_count, 1)
        self.assertContains(response, "未重复执行")

    def test_csrf_protection_is_enabled(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            self.url,
            {"action": "save", "run_time": "08:05", "lookback_days": "3"},
        )

        self.assertEqual(response.status_code, 403)

    def test_recent_twenty_and_selected_detail_are_displayed(self):
        runs = []
        for index in range(21):
            runs.append(
                make_workflow_run(
                    started_at=timezone.now() + timedelta(seconds=index),
                    error_message=f"workflow marker {index}",
                )
            )

        response = self.client.get(f"{self.url}?run={runs[-1].pk}")

        self.assertEqual(len(response.context["recent_runs"]), 20)
        self.assertEqual(response.context["selected_run"], runs[-1])
        self.assertContains(response, "workflow marker 20")
        self.assertNotContains(response, "workflow marker 0")
        self.assertContains(response, "八步执行详情")

    def test_navigation_places_scheduler_under_system_group(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn(
            '<details class="nav-group is-active" data-nav-group="system" open>',
            html,
        )
        self.assertIn(
            '<a class="nav-subitem is-active" href="/system/schedules/" aria-current="page">自动调度</a>',
            html,
        )
        self.assertIn('href="/system/schedules/"', html)
        self.assertNotIn('href="/market-anomaly/"', html)
        self.assertNotIn('href="/analysis/"', html)
        self.assertNotIn('href="/research/"', html)
        self.assertNotIn('href="/reports/"', html)

    def test_unbuilt_routes_remain_unavailable(self):
        for path in (
            "/market-anomaly/",
            "/analysis/",
            "/research/",
            "/reports/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
