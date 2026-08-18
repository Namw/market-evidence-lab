from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.market_funds.models import EtfFlowDaily, StablecoinSupplyDaily
from apps.scheduling.funds_workflow import (
    FundWorkflowAlreadyRunning,
    calculate_next_fund_run,
    claim_due_fund_schedules,
    execute_manual_fund_workflow,
    get_builtin_fund_schedules,
)
from apps.scheduling.models import FundDataSchedule, FundDataWorkflowRun


class ViewAndSchedulingTests(TestCase):
    def test_fund_schedules_use_equivalent_beijing_wall_times(self):
        schedules = {item.task_type: item for item in get_builtin_fund_schedules()}

        self.assertEqual(schedules[FundDataSchedule.TaskType.STABLECOIN].run_time.hour, 14)
        self.assertEqual(schedules[FundDataSchedule.TaskType.ETF].run_time.hour, 14)
        self.assertEqual(
            schedules[FundDataSchedule.TaskType.ETF].supplement_run_time.hour,
            20,
        )
        self.assertEqual(schedules[FundDataSchedule.TaskType.ADDRESSES].run_time.hour, 8)
        self.assertTrue(
            all(item.timezone == "Asia/Shanghai" for item in schedules.values())
        )

    def test_fund_schedule_conversion_preserves_original_execution_instants(self):
        schedules = {item.task_type: item for item in get_builtin_fund_schedules()}

        self.assertEqual(
            calculate_next_fund_run(
                schedules[FundDataSchedule.TaskType.STABLECOIN],
                after=datetime(2026, 8, 5, 5, 59, tzinfo=UTC),
            ),
            datetime(2026, 8, 5, 6, 0, tzinfo=UTC),
        )
        self.assertEqual(
            calculate_next_fund_run(
                schedules[FundDataSchedule.TaskType.ETF],
                after=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
            ),
            datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        )

    def test_pages_show_explicit_empty_state_and_real_database_data(self):
        empty = self.client.get(reverse("market_funds:index"))
        self.assertContains(empty, "尚未采集 DeFiLlama 日频数据")
        StablecoinSupplyDaily.objects.create(
            observation_date=date(2026, 8, 5), chain="Ethereum", stablecoin_symbol="",
            circulating_supply=123, circulating_supply_usd=1230000000,
            source_url="https://example.test", retrieved_at=timezone.now(),
        )
        populated = self.client.get(reverse("market_funds:index"))
        self.assertContains(populated, "$1.23B")

    def test_fund_page_combines_overview_stablecoin_and_etf_sections(self):
        response = self.client.get(reverse("market_funds:index"))

        self.assertContains(response, 'href="#summary"')
        self.assertContains(response, 'id="stablecoins"')
        self.assertContains(response, 'id="etf-flows"')
        self.assertContains(response, "关键资金信号")
        self.assertContains(response, "稳定币供应")
        self.assertContains(response, "ETH ETF 资金流")
        self.assertNotContains(response, "自动采集当前已安全停止")

    def test_fund_charts_expose_axes_tooltips_and_ranked_etf_contributions(self):
        for day, value in ((date(2026, 8, 4), 100), (date(2026, 8, 5), 120)):
            StablecoinSupplyDaily.objects.create(
                observation_date=day,
                chain="Ethereum",
                stablecoin_symbol="",
                circulating_supply=value,
                circulating_supply_usd=value,
                source_url="https://example.test",
                retrieved_at=timezone.now(),
            )
            EtfFlowDaily.objects.create(
                trade_date=day,
                ticker="TOTAL",
                flow_usd=value,
                raw_value=str(value),
                is_total=True,
                source_url="https://example.test",
                retrieved_at=timezone.now(),
            )
        for ticker, flow in (("ETHA", 80), ("FETH", -40), ("CETH", 0), ("QETH", None)):
            EtfFlowDaily.objects.create(
                trade_date=date(2026, 8, 5),
                ticker=ticker,
                flow_usd=flow,
                raw_value="" if flow is None else str(flow),
                source_url="https://example.test",
                retrieved_at=timezone.now(),
            )

        response = self.client.get(reverse("market_funds:index"))

        self.assertContains(response, 'data-fund-chart="area"')
        self.assertContains(response, 'data-fund-chart="bars"')
        self.assertContains(response, 'id="stablecoin-chart-data"')
        self.assertContains(response, 'id="etf-chart-data"')
        self.assertContains(response, "各 ETF 对总净流的贡献")
        self.assertContains(response, "净流入")
        self.assertContains(response, "净流出")
        self.assertContains(response, "明确为零")
        self.assertContains(response, "未公布")
        self.assertEqual(response.context["etf_contribution_groups"]["inflows"][0]["ticker"], "ETHA")
        self.assertEqual(response.context["etf_contribution_groups"]["outflows"][0]["ticker"], "FETH")

    def test_legacy_fund_section_urls_redirect_to_page_anchors(self):
        stablecoins = self.client.get(reverse("market_funds:stablecoins"))
        etf_flows = self.client.get(reverse("market_funds:etf_flows"))

        self.assertEqual(stablecoins.status_code, 302)
        self.assertEqual(stablecoins["Location"], f'{reverse("market_funds:index")}#stablecoins')
        self.assertEqual(etf_flows.status_code, 302)
        self.assertEqual(etf_flows["Location"], f'{reverse("market_funds:index")}#etf-flows')

    def test_addresses_remain_a_separate_page(self):
        response = self.client.get(reverse("market_funds:addresses"))

        self.assertContains(response, "ETH 地址变化")
        self.assertContains(response, "自动采集当前已安全停止")
        self.assertNotContains(response, "关键资金信号")

    def test_navigation_order_and_active_group(self):
        html = self.client.get(reverse("market_funds:index")).content.decode()
        navigation = html.split('<nav class="navigation">', 1)[1].split("</nav>", 1)[0]
        self.assertLess(navigation.index("行情数据观察"), navigation.index("ETH 资金观察"))
        self.assertLess(navigation.index("ETH 资金观察"), navigation.index("新闻观察"))
        self.assertIn('data-nav-group="market-funds" open', navigation)

    def test_schedule_page_unifies_fund_tasks_in_main_task_list(self):
        response = self.client.get(reverse("scheduling:index"))
        self.assertNotContains(response, "独立数据域")
        self.assertContains(response, "任务列表 <span>8</span>", html=True)
        self.assertContains(response, "Ethereum 稳定币供应")
        self.assertContains(response, "ETH ETF 每日资金流")
        self.assertContains(response, "Ethereum 公开地址余额快照")
        self.assertContains(response, "fund-stablecoin-run-dialog")
        self.assertContains(response, "fund-etf-run-dialog")
        self.assertNotContains(response, 'id="fund-addresses-run-dialog"')

    def test_schedule_page_can_enable_allowed_source_but_not_etherscan(self):
        get_builtin_fund_schedules()
        self.client.post(
            reverse("scheduling:index"),
            {"action": "toggle_fund", "task_type": FundDataSchedule.TaskType.STABLECOIN},
        )
        self.assertTrue(
            FundDataSchedule.objects.get(
                task_type=FundDataSchedule.TaskType.STABLECOIN
            ).enabled
        )
        self.client.post(
            reverse("scheduling:index"),
            {"action": "toggle_fund", "task_type": FundDataSchedule.TaskType.ADDRESSES},
        )
        self.assertFalse(
            FundDataSchedule.objects.get(
                task_type=FundDataSchedule.TaskType.ADDRESSES
            ).enabled
        )

    @patch("apps.scheduling.views.execute_manual_fund_workflow")
    def test_schedule_page_can_confirm_and_run_allowed_fund_task(self, execute):
        page = self.client.get(reverse("scheduling:index"))
        run = FundDataWorkflowRun.objects.create(
            task_type=FundDataSchedule.TaskType.STABLECOIN,
            trigger=FundDataWorkflowRun.Trigger.MANUAL,
            status=FundDataWorkflowRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        execute.return_value = run

        response = self.client.post(
            reverse("scheduling:index"),
            {
                "action": "run_fund",
                "task_type": FundDataSchedule.TaskType.STABLECOIN,
                "fund_run_token": page.context["fund_run_token"],
            },
        )

        self.assertRedirects(
            response,
            reverse("market_funds:run_detail", kwargs={"run_id": run.pk}),
            fetch_redirect_response=False,
        )
        execute.assert_called_once_with(FundDataSchedule.TaskType.STABLECOIN)

    def test_claim_is_atomic_and_advances_due_schedule(self):
        schedules = get_builtin_fund_schedules()
        schedule = next(item for item in schedules if item.task_type == FundDataSchedule.TaskType.STABLECOIN)
        now = datetime(2026, 8, 5, 7, tzinfo=UTC)
        schedule.enabled = True
        schedule.next_run_at = now - timedelta(minutes=1)
        schedule.save()
        claimed = claim_due_fund_schedules(now=now)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claim_due_fund_schedules(now=now), [])
        schedule.refresh_from_db()
        self.assertGreater(schedule.next_run_at, now)

    def test_one_running_workflow_per_task_protects_manual_concurrency(self):
        FundDataWorkflowRun.objects.create(
            task_type=FundDataSchedule.TaskType.ADDRESSES,
            trigger=FundDataWorkflowRun.Trigger.MANUAL,
            started_at=timezone.now(),
        )
        with self.assertRaises(FundWorkflowAlreadyRunning):
            execute_manual_fund_workflow(FundDataSchedule.TaskType.ADDRESSES)
