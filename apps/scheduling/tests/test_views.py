from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.utils import timezone

from apps.scheduling.models import (
    DeribitOptionsWorkflowRun,
    SCHEDULE_TIMEZONE,
    NewsWorkflowRun,
    WorkflowRun,
    empty_workflow_details,
)
from apps.scheduling.deribit_workflow import get_builtin_deribit_options_schedule
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


def schedule_local_noon(*, days_from_today: int = 0):
    schedule_zone = ZoneInfo(SCHEDULE_TIMEZONE)
    local_day = timezone.localdate(timezone=schedule_zone) + timedelta(
        days=days_from_today
    )
    return datetime.combine(local_day, time(12), tzinfo=schedule_zone).astimezone(UTC)


class SchedulingPageTests(TestCase):
    url = "/system/schedules/"

    def test_get_returns_200_and_displays_real_executor_state(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "scheduling/index.html")
        self.assertContains(response, "执行器离线")
        self.assertContains(response, "行情原始数据工作流")

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

    def test_deribit_configuration_is_daily_and_uses_prg(self):
        response = self.client.post(
            self.url,
            {
                "action": "save_deribit",
                "enabled": "on",
                "run_time": "08:20",
                "dvol_lookback_days": "5",
            },
        )

        self.assertRedirects(response, self.url)
        schedule = get_builtin_deribit_options_schedule()
        self.assertTrue(schedule.enabled)
        self.assertEqual(schedule.run_time, time(8, 20))
        self.assertEqual(schedule.dvol_lookback_days, 5)
        self.assertEqual(schedule.timezone, SCHEDULE_TIMEZONE)

    @patch("apps.scheduling.views.execute_manual_deribit_options_workflow")
    def test_deribit_manual_execution_uses_token(self, execute):
        execute.return_value = SimpleNamespace(
            status=DeribitOptionsWorkflowRun.Status.SUCCESS,
        )
        token = self.client.get(self.url).context["deribit_run_token"]

        response = self.client.post(
            self.url,
            {"action": "run_deribit", "deribit_run_token": token},
        )

        self.assertRedirects(response, self.url)
        execute.assert_called_once_with(dvol_lookback_days=3)

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

        self.assertRedirects(
            response,
            "/system/schedules/runs/market/88/",
            fetch_redirect_response=False,
        )
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

    def test_run_list_merges_workflow_types_and_detail_displays_failure_reason(self):
        market_run = make_workflow_run(
            status=WorkflowRun.Status.FAILED,
            error_message="Binance 请求超时",
            started_at=timezone.now(),
        )
        news_run = NewsWorkflowRun.objects.create(
            trigger=NewsWorkflowRun.Trigger.SCHEDULED,
            status=NewsWorkflowRun.Status.PARTIAL,
            safe_error_summary="新闻分析接口不可用",
            started_at=timezone.now() + timedelta(seconds=1),
            finished_at=timezone.now() + timedelta(seconds=2),
        )

        response = self.client.get("/system/schedules/runs/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["run_items"][0]["kind"], "news")
        self.assertContains(response, "Binance 请求超时")
        self.assertContains(response, "新闻分析接口不可用")

        detail = self.client.get(f"/system/schedules/runs/market/{market_run.pk}/")
        self.assertContains(detail, "异常说明")
        self.assertContains(detail, "Binance 请求超时")
        self.assertContains(detail, "八步执行详情")
        self.assertEqual(news_run.status, NewsWorkflowRun.Status.PARTIAL)

    def test_run_list_can_filter_each_workflow_type(self):
        make_workflow_run()
        NewsWorkflowRun.objects.create(
            trigger=NewsWorkflowRun.Trigger.MANUAL,
            status=NewsWorkflowRun.Status.SUCCESS,
            started_at=timezone.now(),
        )

        response = self.client.get("/system/schedules/runs/?task=news")

        self.assertEqual(len(response.context["run_items"]), 1)
        self.assertEqual(response.context["run_items"][0]["kind"], "news")

    def test_run_list_defaults_to_today_in_schedule_timezone(self):
        today_run = make_workflow_run(started_at=schedule_local_noon())
        make_workflow_run(started_at=schedule_local_noon(days_from_today=-1))

        response = self.client.get("/system/schedules/runs/")

        self.assertEqual(
            [item["id"] for item in response.context["run_items"]],
            [today_run.pk],
        )
        self.assertContains(response, "今日采集情况")
        self.assertContains(response, "Asia/Shanghai")
        local_today = timezone.localdate(timezone=ZoneInfo(SCHEDULE_TIMEZONE))
        recent_start = (local_today - timedelta(days=2)).isoformat()
        self.assertContains(
            response,
            f"?task=all&amp;start_date={recent_start}&amp;end_date={local_today.isoformat()}",
        )
        self.assertContains(response, "最近三天")

    def test_run_list_accepts_inclusive_date_range_and_preserves_task_filter(self):
        older_run = make_workflow_run(
            started_at=schedule_local_noon(days_from_today=-2)
        )
        today_run = make_workflow_run(started_at=schedule_local_noon())
        local_today = timezone.localdate(timezone=ZoneInfo(SCHEDULE_TIMEZONE))

        response = self.client.get(
            "/system/schedules/runs/",
            {
                "task": "market",
                "start_date": (local_today - timedelta(days=2)).isoformat(),
                "end_date": local_today.isoformat(),
            },
        )

        self.assertEqual(
            {item["id"] for item in response.context["run_items"]},
            {older_run.pk, today_run.pk},
        )
        self.assertEqual(response.context["selected_task"], "market")
        self.assertContains(response, 'name="task" value="market"')

    def test_invalid_date_range_falls_back_to_today_with_message(self):
        today_run = make_workflow_run(started_at=schedule_local_noon())

        response = self.client.get(
            "/system/schedules/runs/?start_date=2026-08-03&end_date=2026-08-01"
        )

        self.assertEqual(response.context["run_items"][0]["id"], today_run.pk)
        self.assertContains(response, "开始日期不能晚于结束日期")

    def test_legacy_detail_query_redirects_to_new_detail_url(self):
        run = make_workflow_run()

        response = self.client.get(f"{self.url}?run={run.pk}")

        self.assertRedirects(
            response,
            f"/system/schedules/runs/market/{run.pk}/",
            fetch_redirect_response=False,
        )

    def test_invalid_schedule_form_marks_configuration_dialog_for_reopening(self):
        response = self.client.post(
            self.url,
            {"action": "save", "run_time": "08:05", "lookback_days": "31"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_dialog"], "market-config-dialog")
        self.assertContains(response, 'id="market-config-dialog" data-open-dialog')

    def test_navigation_places_scheduler_under_collection_group(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn(
            '<details class="nav-group is-active" data-nav-group="collection" open>',
            html,
        )
        self.assertIn(
            '<a class="nav-subitem is-active" href="/system/schedules/" aria-current="page">自动调度</a>',
            html,
        )
        self.assertIn(
            '<a class="nav-subitem" href="/system/schedules/runs/">调度情况</a>',
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
