from datetime import UTC, date, datetime, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.market_funds.models import StablecoinSupplyDaily
from apps.scheduling.funds_workflow import (
    FundWorkflowAlreadyRunning,
    claim_due_fund_schedules,
    execute_manual_fund_workflow,
    get_builtin_fund_schedules,
)
from apps.scheduling.models import FundDataSchedule, FundDataWorkflowRun


class ViewAndSchedulingTests(TestCase):
    def test_pages_show_explicit_empty_state_and_real_database_data(self):
        empty = self.client.get(reverse("market_funds:index"))
        self.assertContains(empty, "尚未采集 DeFiLlama 日频数据")
        StablecoinSupplyDaily.objects.create(
            observation_date=date(2026, 8, 5), chain="Ethereum", stablecoin_symbol="",
            circulating_supply=123, circulating_supply_usd=1230000000,
            source_url="https://example.test", retrieved_at=timezone.now(),
        )
        populated = self.client.get(reverse("market_funds:stablecoins"))
        self.assertContains(populated, "$1.23B")

    def test_navigation_order_and_active_group(self):
        html = self.client.get(reverse("market_funds:index")).content.decode()
        navigation = html.split('<nav class="navigation">', 1)[1].split("</nav>", 1)[0]
        self.assertLess(navigation.index("行情数据观察"), navigation.index("链上资金观察"))
        self.assertLess(navigation.index("链上资金观察"), navigation.index("新闻观察"))
        self.assertIn('data-nav-group="market-funds" open', navigation)

    def test_schedule_page_has_independent_fund_block_and_three_tasks(self):
        response = self.client.get(reverse("scheduling:index"))
        self.assertContains(response, "链上资金数据")
        self.assertContains(response, "Ethereum 稳定币供应")
        self.assertContains(response, "ETH ETF 每日资金流")
        self.assertContains(response, "Ethereum 公开地址余额快照")

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
