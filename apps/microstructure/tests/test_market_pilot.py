import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.microstructure.market_pilot import (
    FourHourBlock,
    _parse_ai_content,
    run_market_pilot,
    select_representative_indices,
)
from apps.microstructure.models import MarketPilotReport, MarketPilotRun


def block(index: int, return_pct: str) -> FourHourBlock:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index * 4)
    open_price = Decimal("100")
    close_price = open_price * (Decimal("1") + Decimal(return_pct) / 100)
    return FourHourBlock(
        start=start,
        end=start + timedelta(hours=4),
        open=open_price,
        high=max(open_price, close_price),
        low=min(open_price, close_price),
        close=close_price,
        quote_volume=Decimal("1000"),
        taker_buy_quote_volume=Decimal("500"),
    )


class MarketPilotTests(SimpleTestCase):
    def test_selection_keeps_shocks_and_both_calm_directions(self):
        returns = ["0.5"] * 20
        returns[8] = "3"
        returns[10] = "-2.5"
        returns[7] = "0.01"
        returns[9] = "-0.02"
        selected = select_representative_indices(
            [block(index, value) for index, value in enumerate(returns)]
        )

        self.assertEqual(selected, [7, 8, 9, 10])

    def test_ai_parser_requires_exact_windows_and_supported_labels(self):
        start = "2026-08-19T12:00:00+00:00"
        content = json.dumps(
            {
                "analyses": [
                    {
                        "window_start": start,
                        "mechanism": "mixed",
                        "confidence": "low",
                    }
                ]
            }
        )

        parsed = _parse_ai_content(content, {start})

        self.assertEqual(parsed[0]["mechanism"], "mixed")

    def test_ai_parser_rejects_missing_window(self):
        with self.assertRaisesMessage(ValueError, "window set"):
            _parse_ai_content('{"analyses": []}', {"missing"})


class MarketPilotPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        started_at = datetime(2026, 8, 25, 0, tzinfo=UTC)
        cls.pilot_run = MarketPilotRun.objects.create(
            symbol="ETHUSDT",
            prompt_version="market-four-hour-pilot-v1",
            configured_model="deepseek-chat",
            actual_models=["deepseek-chat"],
            status=MarketPilotRun.Status.SUCCESS,
            window_count=1,
            request_count=1,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=5),
        )
        cls.report = MarketPilotReport.objects.create(
            run=cls.pilot_run,
            symbol="ETHUSDT",
            window_start=started_at - timedelta(hours=4),
            window_end=started_at,
            selection_reason=MarketPilotReport.SelectionReason.SHOCK,
            mechanism=MarketPilotReport.Mechanism.TREND_EXPANSION,
            confidence=MarketPilotReport.Confidence.MEDIUM,
            input_snapshot={
                "market_evidence": {
                    "price": {"open": 4000, "close": 4320, "return_pct": 8},
                    "volume": {},
                    "open_interest": {},
                    "funding": {},
                    "dvol": {},
                    "microstructure": {},
                },
                "news_available_before_window_end": [],
                "known_data_limitations": ["测试数据限制"],
            },
            ai_analysis={
                "trigger_assessment": "价格和成交量同步扩张。",
                "amplifier_assessment": "持仓变化放大波动。",
                "supporting_evidence": ["四小时上涨 8%"],
                "contrary_evidence": [],
                "continuation_conditions": ["成交量保持"],
                "adjustment_conditions": ["成交量衰减"],
                "limitations": [],
            },
            future_outcomes={
                "future_4h_return_pct": 1.2,
                "future_12h_return_pct": 2.3,
                "future_24h_return_pct": -0.4,
            },
        )

    def test_report_list_links_to_detail(self):
        response = self.client.get(reverse("microstructure:market_pilot_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI 市场预演")
        self.assertContains(
            response,
            reverse("microstructure:market_pilot_detail", args=[self.report.pk]),
        )
        self.assertContains(response, "趋势扩张")

    def test_report_detail_shows_snapshot_analysis_and_outcomes(self):
        response = self.client.get(
            reverse("microstructure:market_pilot_detail", args=[self.report.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "价格和成交量同步扩张")
        self.assertContains(response, "四小时上涨 8%")
        self.assertContains(response, "后续 24h")
        self.assertContains(response, "未来结果隔离")

    def test_unknown_report_returns_404(self):
        response = self.client.get(
            reverse("microstructure:market_pilot_detail", args=[999_999])
        )

        self.assertEqual(response.status_code, 404)

    @patch("apps.microstructure.market_pilot.analyze_with_deepseek")
    @patch("apps.microstructure.market_pilot.build_pilot_inputs")
    def test_pilot_run_persists_ai_result_and_future_outcome(
        self, build_inputs, analyze
    ):
        window_start = "2026-08-24T20:00:00+00:00"
        window_end = "2026-08-25T00:00:00+00:00"
        pilot_input = {
            "window_start": window_start,
            "window_end": window_end,
            "selection_reason": "calm_control",
            "market_evidence": {"price": {"return_pct": 0.02}},
        }
        future = {
            "window_start": window_start,
            "window_end": window_end,
            "future_4h_return_pct": 1.0,
            "future_12h_return_pct": 2.0,
            "future_24h_return_pct": 3.0,
        }
        analysis = {
            "window_start": window_start,
            "mechanism": "insufficient_evidence",
            "confidence": "low",
            "trigger_assessment": "证据不足。",
        }
        build_inputs.return_value = ([pilot_input], [future])
        analyze.return_value = (
            [analysis],
            {
                "configured_model": "deepseek-chat",
                "actual_models": ["deepseek-chat"],
                "request_count": 1,
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

        result = run_market_pilot("ETHUSDT")

        persisted_run = MarketPilotRun.objects.get(pk=result["run_id"])
        persisted_report = persisted_run.reports.get()
        self.assertEqual(persisted_run.status, MarketPilotRun.Status.SUCCESS)
        self.assertEqual(persisted_run.total_tokens, 18)
        self.assertEqual(
            persisted_report.mechanism,
            MarketPilotReport.Mechanism.INSUFFICIENT,
        )
        self.assertEqual(persisted_report.input_snapshot, pilot_input)
        self.assertEqual(persisted_report.future_outcomes, future)
