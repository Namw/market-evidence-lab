from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

from django.test import TestCase, override_settings

from apps.microstructure.market_monitor import (
    monitor_market_windows,
    update_due_outcomes,
)
from apps.microstructure.models import (
    MarketMinute,
    MarketPilotReport,
    MarketPilotWindowCheck,
)


WINDOW_START = datetime(2026, 8, 25, 8, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(hours=4)


def create_minute_history(*, anomaly_return: Decimal) -> None:
    history_start = WINDOW_START - timedelta(hours=24)
    rows = []
    for index in range(28 * 60):
        minute_start = history_start + timedelta(minutes=index)
        in_current = minute_start >= WINDOW_START
        open_price = Decimal("100")
        close_price = Decimal("100")
        if in_current and minute_start == WINDOW_END - timedelta(minutes=1):
            close_price = Decimal("100") * (Decimal("1") + anomaly_return / 100)
        rows.append(
            MarketMinute(
                symbol="ETHUSDT",
                minute_start=minute_start,
                minute_end=minute_start + timedelta(minutes=1),
                open_price=open_price,
                high_price=max(open_price, close_price),
                low_price=min(open_price, close_price),
                close_price=close_price,
                quote_volume=Decimal("1000"),
                taker_buy_quote=Decimal("520"),
                taker_sell_quote=Decimal("480"),
                delta_quote=Decimal("40"),
                kline_closed=True,
            )
        )
    MarketMinute.objects.bulk_create(rows, batch_size=500)


def analysis_result(inputs):
    analyses = [
        {
            "window_start": item["window_start"],
            "mechanism": "trend_expansion",
            "confidence": "medium",
            "trigger_assessment": "成交量与价格同步扩张。",
            "amplifier_assessment": "主动买入放大行情。",
            "supporting_evidence": ["四小时涨幅达到阈值"],
            "contrary_evidence": [],
            "continuation_conditions": ["成交量延续"],
            "adjustment_conditions": ["成交量衰减"],
            "limitations": [],
        }
        for item in inputs
    ]
    return analyses, {
        "actual_models": ["deepseek-test"],
        "request_count": 1,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"errcode": 0, "errmsg": "ok"}


@override_settings(
    MARKET_PILOT_WECHAT_WEBHOOK_URL="",
    MARKET_PILOT_PUBLIC_BASE_URL="",
)
class MarketMonitorTests(TestCase):
    def test_normal_window_is_recorded_without_calling_ai(self):
        create_minute_history(anomaly_return=Decimal("1"))
        analyzer = Mock()

        run = monitor_market_windows(
            now=WINDOW_END + timedelta(minutes=10),
            max_windows=1,
            analyzer=analyzer,
        )

        self.assertEqual(run.status, "success")
        check = MarketPilotWindowCheck.objects.get()
        self.assertEqual(check.status, MarketPilotWindowCheck.Status.NORMAL)
        self.assertEqual(MarketPilotReport.objects.count(), 0)
        analyzer.assert_not_called()

    @override_settings(
        MARKET_PILOT_WECHAT_WEBHOOK_URL="https://example.test/webhook",
        MARKET_PILOT_PUBLIC_BASE_URL="http://example.test",
    )
    def test_anomaly_creates_report_pushes_once_and_is_idempotent(self):
        create_minute_history(anomaly_return=Decimal("3"))
        analyzer = Mock(side_effect=analysis_result)
        client = Mock()
        client.post.return_value = FakeResponse()

        first = monitor_market_windows(
            now=WINDOW_END + timedelta(minutes=10),
            max_windows=1,
            analyzer=analyzer,
            notification_client=client,
        )
        second = monitor_market_windows(
            now=WINDOW_END + timedelta(minutes=20),
            max_windows=1,
            analyzer=analyzer,
            notification_client=client,
        )

        report = MarketPilotReport.objects.get()
        check = MarketPilotWindowCheck.objects.get()
        self.assertEqual(first.request_count, 1)
        self.assertEqual(second.window_count, 0)
        self.assertEqual(check.report, report)
        self.assertEqual(report.status, MarketPilotReport.Status.AWAITING_OUTCOMES)
        self.assertEqual(
            report.notification_status,
            MarketPilotReport.NotificationStatus.SENT,
        )
        self.assertEqual(analyzer.call_count, 1)
        self.assertEqual(client.post.call_count, 1)
        payload = client.post.call_args.kwargs["json"]
        self.assertNotIn("webhook", payload["markdown"]["content"])
        self.assertIn(f"market-pilot/{report.id}/", payload["markdown"]["content"])

    def test_outcomes_are_added_only_after_each_horizon_exists(self):
        create_minute_history(anomaly_return=Decimal("3"))
        run = monitor_market_windows(
            now=WINDOW_END + timedelta(minutes=10),
            max_windows=1,
            analyzer=analysis_result,
        )
        report = run.reports.get()
        for horizon, close in ((4, "104"), (12, "105"), (24, "102")):
            target = WINDOW_END + timedelta(hours=horizon)
            MarketMinute.objects.create(
                symbol="ETHUSDT",
                minute_start=target - timedelta(minutes=1),
                minute_end=target,
                open_price=Decimal(close),
                high_price=Decimal(close),
                low_price=Decimal(close),
                close_price=Decimal(close),
                kline_closed=True,
            )

        update_due_outcomes(now=WINDOW_END + timedelta(hours=24))

        report.refresh_from_db()
        self.assertEqual(report.status, MarketPilotReport.Status.COMPLETED)
        self.assertEqual(set(report.future_outcomes), {
            "future_4h_return_pct",
            "future_12h_return_pct",
            "future_24h_return_pct",
        })
