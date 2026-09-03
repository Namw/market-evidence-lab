import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain.agents.middleware import AgentMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from apps.microstructure.models import MarketMinute
from apps.trading_assistant.agent import context_messages, prepare_turn, run_agent, validated_report
from apps.trading_assistant.data import baseline, capture_snapshot, compare, summary, trade_plan
from apps.trading_assistant.models import AnalysisTurn, Conversation, ToolExecution
from apps.trading_assistant.schemas import checkpoint_serializer
from apps.trading_assistant.services import mark_success, submit_turn
from apps.trading_assistant.tools import make_tools
from apps.trading_assistant.report_recovery import ReportGenerationError


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class InterruptedModel(ToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.i >= 1:
            raise ConnectionError("simulated provider interruption")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class ProtocolCheckingModel(ToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        pending = set()
        for message in messages:
            if message.type == "ai":
                if pending:
                    raise ValueError("unanswered tool calls")
                pending = {call["id"] for call in message.tool_calls + message.invalid_tool_calls}
            elif message.type == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("unexpected tool response")
                pending.remove(message.tool_call_id)
            elif pending:
                raise ValueError("unanswered tool calls")
        if pending:
            raise ValueError("unanswered tool calls")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def broken_report():
    return AIMessage(content="", invalid_tool_calls=[{
        "name": "TradingReport", "id": "broken_json", "args": '{"long": <DSML>', "error": "invalid JSON",
    }], tool_calls=[
        {"name": "TradingReport", "id": "extra_1", "args": {"stance": "short"}},
        {"name": "TradingReport", "id": "extra_2", "args": {"stance": "wait"}},
    ], usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}, response_metadata={"model_name": "test-model"})


def report_payload(**changes):
    scene = {"assessment": "证据尚不充分", "supporting": ["主动买入占比 60%"], "opposing": ["尚未验证后续收益"], "condition": "等待价格与主动成交同时确认"}
    return {"stance": "wait", "summary": "当前更适合观望。", "long": scene, "short": scene, "wait": scene, "evidence_ids": ["E0"], "plan_ids": [], "follow_up": "可继续讨论候选入场价。", **changes}


class AssistantTests(TransactionTestCase):
    def setUp(self):
        self.cutoff = timezone.now().replace(second=0, microsecond=0)
        MarketMinute.objects.bulk_create([
            MarketMinute(
                symbol="ZECUSDT", minute_start=self.cutoff - timedelta(minutes=300-i),
                minute_end=self.cutoff - timedelta(minutes=299-i),
                open_price=100 + i * .01, high_price=100.1 + i * .01,
                low_price=99.9 + i * .01, close_price=100.005 + i * .01,
                quote_volume=100, taker_buy_quote=60, taker_sell_quote=40,
                delta_quote=20, bid_depth_mean=50000, ask_depth_mean=48000,
                imbalance_top5_mean=.1, spread_bps_mean=2, spread_bps_p95=3,
                coverage_ratio=1, kline_closed=True, future_5m_return=999,
            ) for i in range(300)
        ])
        self.conversation = Conversation.objects.create(symbol="ZECUSDT")

    def turn(self, question="我想开仓", **kwargs):
        return AnalysisTurn.objects.create(conversation=self.conversation, request_id=uuid.uuid4(), question=question, **kwargs)

    def test_snapshot_excludes_future_unclosed_and_other_symbols(self):
        MarketMinute.objects.create(symbol="ETHUSDT", minute_start=self.cutoff-timedelta(minutes=1), minute_end=self.cutoff, close_price=50000, kline_closed=True)
        MarketMinute.objects.create(symbol="ZECUSDT", minute_start=self.cutoff, minute_end=self.cutoff+timedelta(minutes=1), close_price=80000, kline_closed=True)
        row = MarketMinute.objects.filter(symbol="ZECUSDT", minute_start=self.cutoff-timedelta(minutes=2)).first()
        row.kline_closed = False
        row.save(update_fields=["kline_closed"])
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        self.assertEqual(len(snapshot.rows), 299)
        self.assertNotIn("future_5m_return", json.dumps(snapshot.rows))
        self.assertLess(snapshot.quality["reference_price"], 200)
        self.assertEqual(summary(snapshot, 120)["observed_minutes"], 119)

    def test_snapshot_is_frozen_and_windows_are_half_open(self):
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        self.assertEqual(summary(snapshot, 120)["observed_minutes"], 120)
        self.assertEqual(summary(snapshot, 15)["quote_volume_usdt"], 1500)
        MarketMinute.objects.filter(symbol="ZECUSDT").update(taker_buy_quote=100)
        self.assertEqual(summary(snapshot, 15)["buy_share_pct"], 60)
        values = compare(snapshot, 15, 45)
        self.assertEqual(values["volume_per_observed_minute_ratio"], 1)
        self.assertEqual(values["buy_share_change_percentage_points"], 0)

    def test_query_bounds_and_tool_allowlist(self):
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        with self.assertRaises(ValueError):
            summary(snapshot, 1440, 1)
        turn = self.turn(snapshot=snapshot)
        tools = {tool.name: tool for tool in make_tools(turn)}
        self.assertEqual(set(tools), {"get_data_quality", "get_microstructure_summary", "get_market_series", "compare_windows", "build_trade_plan"})
        result = tools["get_market_series"].invoke({"minutes": 1440, "bucket_minutes": 1})
        self.assertIn("error", result)

    def test_missing_book_data_blocks_plans(self):
        MarketMinute.objects.update(coverage_ratio=.2)
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        self.assertFalse(snapshot.quality["usable_for_entry"])
        self.assertFalse(trade_plan(snapshot, direction="long", horizon_minutes=240)["available"])

    def test_stale_data_blocks_plans(self):
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        plan = trade_plan(snapshot, direction="long", horizon_minutes=240, now=self.cutoff+timedelta(minutes=6))
        self.assertFalse(plan["available"])

    def test_recent_book_outage_blocks_even_when_overall_coverage_is_good(self):
        MarketMinute.objects.filter(minute_start__gte=self.cutoff-timedelta(minutes=10)).update(coverage_ratio=0)
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        self.assertGreater(snapshot.quality["recent_120m"]["book_sampling_coverage"], .8)
        self.assertFalse(snapshot.quality["usable_for_entry"])
        self.assertIn("最新有效盘口", " ".join(snapshot.quality["reasons"]))

    def test_empty_snapshot_has_no_reference_price(self):
        snapshot = capture_snapshot("ETHUSDT", now=self.cutoff)
        self.assertIsNone(snapshot.quality["reference_price"])
        self.assertFalse(snapshot.quality["usable_for_entry"])

    def test_long_short_plans_and_custom_entry_arithmetic(self):
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        for direction, horizon in ((direction, horizon) for direction in ("long", "short") for horizon in (240, 480, 1440)):
            plan = trade_plan(snapshot, direction=direction, horizon_minutes=horizon, entry_price=102)
            self.assertTrue(plan["available"])
            self.assertEqual(plan["entry_price"], 102)
            if direction == "long":
                self.assertLess(max(plan["stop_zone"]), 102)
                self.assertGreater(min(plan["take_profit_zone"]), 102)
            else:
                self.assertGreater(min(plan["stop_zone"]), 102)
                self.assertLess(max(plan["take_profit_zone"]), 102)
            cost = 102 * .0012
            conservative_rr = min(abs(v-102)-cost for v in plan["take_profit_zone"]) / max(abs(v-102)+cost for v in plan["stop_zone"])
            self.assertAlmostEqual(plan["risk_reward_after_cost_range"][0], conservative_rr, places=6)
            self.assertIsNone(plan["win_rate"])

    def test_gap_in_atr_window_rejects_plan(self):
        MarketMinute.objects.filter(minute_start=self.cutoff-timedelta(minutes=4)).delete()
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        self.assertTrue(snapshot.quality["usable_for_entry"])
        self.assertFalse(trade_plan(snapshot, direction="long", horizon_minutes=240)["available"])

    def test_invalid_or_far_entry_rejected(self):
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        for price in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                trade_plan(snapshot, direction="long", horizon_minutes=240, entry_price=price)
        self.assertFalse(trade_plan(snapshot, direction="long", horizon_minutes=240, entry_price=2000)["available"])

    def test_repeated_tool_calls_reuse_evidence(self):
        turn = self.turn(snapshot=capture_snapshot("ZECUSDT", now=self.cutoff))
        tool = {item.name: item for item in make_tools(turn)}["compare_windows"]
        first = tool.invoke({"recent_minutes": 15, "previous_minutes": 45})
        second = tool.invoke({"recent_minutes": 15, "previous_minutes": 45})
        self.assertEqual(first, second)
        self.assertEqual(turn.tool_executions.count(), 1)

    def test_report_requires_real_evidence_and_computed_plans(self):
        turn = self.turn(snapshot=capture_snapshot("ZECUSDT", now=self.cutoff))
        for payload in (report_payload(evidence_ids=["E99999"]), report_payload(plan_ids=["E0"])):
            with self.assertRaises(ValueError):
                validated_report(turn, payload)
        tool = {item.name: item for item in make_tools(turn)}["build_trade_plan"]
        plan = tool.invoke({"direction": "long", "horizon_minutes": 240})
        result = validated_report(turn, report_payload(plan_ids=[plan["evidence_id"]]))
        self.assertEqual(result["plans"][0], plan)

    def test_low_quality_forces_wait_and_clears_price_plans(self):
        snapshot = capture_snapshot("ZECUSDT", now=self.cutoff)
        snapshot.quality["usable_for_entry"] = False
        snapshot.quality["reasons"] = ["覆盖不足"]
        turn = self.turn(snapshot=snapshot)
        report = validated_report(turn, report_payload(stance="long"))
        self.assertEqual(report["stance"], "wait")
        self.assertEqual(report["plans"], [])
        self.assertEqual(report["guard_notes"], ["覆盖不足"])

    def test_idempotent_submission_and_single_inflight_turn(self):
        request_id = uuid.uuid4()
        args = {"question": "开多？", "request_id": request_id, "refresh_data": True, "horizon_minutes": 240}
        first = submit_turn(self.conversation.pk, **args)
        second = submit_turn(self.conversation.pk, **args)
        self.assertEqual(first.pk, second.pk)
        with self.assertRaises(ValueError):
            submit_turn(self.conversation.pk, **{**args, "request_id": uuid.uuid4()})

    def test_followup_retains_report_and_reuses_snapshot_when_requested(self):
        first = self.turn()
        prepare_turn(first)
        mark_success(first, {"summary": "原报告", "plans": [{"entry_price": 102}]})
        followup = self.turn("那止损呢？", refresh_data=False)
        prepare_turn(followup)
        self.assertEqual(first.snapshot_id, followup.snapshot_id)
        serialized = str(context_messages(followup))
        self.assertIn("原报告", serialized)
        self.assertIn("102", serialized)
        self.assertIn("那止损呢", serialized)

    def test_original_context_is_saved_and_not_recomputed(self):
        turn = self.turn()
        prepare_turn(turn)
        original = str(context_messages(turn))
        turn.snapshot.quality["reference_price"] = 999999
        self.assertEqual(original, str(context_messages(turn)))
        turn.refresh_from_db()
        self.assertIn("baseline_evidence", turn.input_context["input"])

    def test_report_horizon_tracks_explicit_override(self):
        turn = self.turn(snapshot=capture_snapshot("ZECUSDT", now=self.cutoff), horizon_minutes=240)
        report = validated_report(turn, report_payload(horizon_minutes=480))
        mark_success(turn, report)
        self.conversation.refresh_from_db()
        self.assertEqual(report["horizon_minutes"], 480)
        self.assertEqual(self.conversation.horizon_minutes, 480)

    @override_settings(TRADING_ASSISTANT_API_KEY="test", SOURCE_PROXY_URL="")
    def test_real_framework_tool_loop_and_checkpoint_resume(self):
        turn = self.turn()
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "compare_windows", "args": {"recent_minutes": 15, "previous_minutes": 45}, "id": "call_1", "type": "tool_call"}]),
            AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": report_payload(), "id": "report_1", "type": "tool_call"}]),
        ])
        saver = InMemorySaver(serde=checkpoint_serializer())
        result = run_agent(turn, saver, model=model)
        self.assertEqual(result["stance"], "wait")
        self.assertEqual(turn.tool_executions.count(), 1)
        turn.refresh_from_db()
        count = turn.usage["model_calls"]
        recovered = run_agent(turn, saver, model=model)
        turn.refresh_from_db()
        self.assertEqual(result, recovered)
        self.assertEqual(count, turn.usage["model_calls"])
        mark_success(turn, result)
        followup = self.turn("那如果开空呢？")
        followup_model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": report_payload(summary="延续 ZEC 的讨论，开空仍需确认。"), "id": "report_2", "type": "tool_call"}])])
        following = run_agent(followup, saver, model=followup_model)
        self.assertIn("开空", following["summary"])
        self.assertIn("我想开仓", str(followup.input_context))

    @override_settings(TRADING_ASSISTANT_API_KEY="test", SOURCE_PROXY_URL="")
    def test_interrupted_framework_resumes_without_repeating_completed_tool(self):
        turn = self.turn()
        model = InterruptedModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "compare_windows", "args": {}, "id": "before_failure", "type": "tool_call"}]),
            AIMessage(content="unused"),
        ])
        saver = InMemorySaver(serde=checkpoint_serializer())
        with self.assertRaises(ConnectionError):
            run_agent(turn, saver, model=model)
        self.assertEqual(turn.tool_executions.count(), 1)
        original_snapshot = turn.snapshot_id
        recovery = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": report_payload(), "id": "after_recovery", "type": "tool_call"}])])
        result = run_agent(turn, saver, model=recovery)
        self.assertEqual(result["stance"], "wait")
        self.assertEqual(turn.snapshot_id, original_snapshot)
        self.assertEqual(turn.tool_executions.count(), 1)

    def test_history_keeps_older_messages_accessible(self):
        for index in range(23):
            self.turn(f"历史问题 {index}", status=AnalysisTurn.Status.SUCCEEDED)
        url = reverse("trading_assistant:conversation_detail", args=[self.conversation.pk])
        first = self.client.get(url).json()
        older = self.client.get(url, {"page": 2}).json()
        self.assertEqual(len(first["turns"]), 20)
        self.assertEqual(first["next_page"], 2)
        self.assertEqual(len(older["turns"]), 3)
        self.assertEqual(older["turns"][0]["question"], "历史问题 0")

    def test_ui_api_history_pagination_and_duplicate_post(self):
        self.assertContains(self.client.get(reverse("trading_assistant:index")), "开仓分析助手")
        self.assertContains(self.client.get(reverse("core:home")), 'href="/trading-assistant/"')
        url = reverse("trading_assistant:send_message", args=[self.conversation.pk])
        payload = {"question": "开仓分析", "request_id": str(uuid.uuid4()), "refresh_data": True, "horizon_minutes": 240}
        first = self.client.post(url, payload, content_type="application/json")
        second = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["turn"]["id"], second.json()["turn"]["id"])
        response = self.client.get(reverse("trading_assistant:conversation_detail", args=[self.conversation.pk]))
        self.assertEqual(response.json()["turns"][0]["question"], "开仓分析")
        self.assertFalse(response.json()["worker_online"])

    def test_invalid_input_and_csrf(self):
        url = reverse("trading_assistant:send_message", args=[self.conversation.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        for body in ([], {"question": ""}, {"question": "x" * 4001}, {"question": "x", "request_id": "invalid"}):
            self.assertEqual(self.client.post(url, json.dumps(body), content_type="application/json").status_code, 400)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(url, {}, content_type="application/json").status_code, 403)

    def test_trace_explains_saved_tools_and_keeps_raw_evidence(self):
        turn = self.turn()
        prepare_turn(turn)
        context_messages(turn)
        tool = {item.name: item for item in make_tools(turn)}["compare_windows"]
        tool.invoke({"recent_minutes": 15, "previous_minutes": 45})
        tool.invoke({"recent_minutes": 15, "previous_minutes": 45})
        turn.started_at = timezone.now() - timedelta(seconds=3)
        turn.usage = {"model_calls": 2}
        turn.save(update_fields=["started_at", "usage"])
        mark_success(turn, {"evidence_ids": ["E0"], "guard_notes": []})
        payload = self.client.get(reverse("trading_assistant:evidence", args=[turn.pk])).json()
        trace = payload["trace"]
        self.assertEqual(trace["status"], "succeeded")
        self.assertEqual(trace["model_calls"], 2)
        self.assertEqual(trace["tool_records"], 1)
        self.assertEqual([item["id"] for item in trace["steps"][:3]], ["question", "snapshot", "context"])
        saved_tool = trace["steps"][3]
        self.assertEqual(saved_tool["details"]["工具参数"], {"recent_minutes": 15, "previous_minutes": 45})
        self.assertEqual(saved_tool["details"]["工具返回"], payload["tools"][0]["result"])
        self.assertEqual(payload["baseline"], turn.input_context["input"]["baseline_evidence"])
        self.assertGreaterEqual(trace["elapsed_seconds"], 3)
        self.assertNotIn("prompt_text", payload)

    def test_trace_does_not_invent_steps_for_queued_or_failed_turns(self):
        turn = self.turn()
        url = reverse("trading_assistant:evidence", args=[turn.pk])
        trace = self.client.get(url).json()["trace"]
        self.assertEqual([step["id"] for step in trace["steps"]], ["question", "outcome"])
        self.assertEqual(trace["steps"][-1]["status"], "pending")
        self.assertIsNone(trace["elapsed_seconds"])
        turn.status = AnalysisTurn.Status.FAILED
        turn.safe_error = "数据库暂时不可用"
        turn.save(update_fields=["status", "safe_error"])
        trace = self.client.get(url).json()["trace"]
        self.assertEqual(trace["steps"][-1]["description"], "数据库暂时不可用")
        self.assertEqual(trace["steps"][-1]["status"], "error")

    @override_settings(TRADING_ASSISTANT_API_KEY="test", SOURCE_PROXY_URL="")
    def test_malformed_report_recovers_without_losing_tool_results(self):
        turn = self.turn()
        model = ProtocolCheckingModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "compare_windows", "args": {}, "id": "comparison"}]),
            broken_report(),
            AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": report_payload(), "id": "fixed"}]),
        ])
        result = run_agent(turn, InMemorySaver(serde=checkpoint_serializer()), model=model)
        self.assertEqual(result["stance"], "wait")
        self.assertEqual(turn.tool_executions.count(), 1)
        turn.refresh_from_db()
        self.assertEqual(turn.usage["model_calls"], 3)
        self.assertEqual(turn.usage["format_retries"], 1)
        self.assertEqual(len(turn.usage["format_recovery_events"]), 1)
        self.assertEqual(turn.usage["token_usage"]["test-model"]["total_tokens"], 110)

    @override_settings(TRADING_ASSISTANT_API_KEY="test", SOURCE_PROXY_URL="")
    def test_format_retries_are_bounded_and_usage_saved_on_failure(self):
        turn = self.turn()
        model = ProtocolCheckingModel(responses=[broken_report(), broken_report(), broken_report()])
        with self.assertRaisesMessage(ReportGenerationError, "自动修复次数已用尽"):
            run_agent(turn, InMemorySaver(serde=checkpoint_serializer()), model=model)
        turn.refresh_from_db()
        self.assertEqual(turn.usage["model_calls"], 3)
        self.assertEqual(turn.usage["format_retries"], 2)
        self.assertEqual(turn.usage["token_usage"]["test-model"]["total_tokens"], 330)

    @override_settings(TRADING_ASSISTANT_API_KEY="test", SOURCE_PROXY_URL="")
    def test_resume_repairs_old_poisoned_checkpoint_before_provider_call(self):
        class LegacyRecovery(AgentMiddleware):
            def __init__(self, turn):
                pass

            def repair(self, messages):
                return None

        turn = self.turn()
        saver = InMemorySaver(serde=checkpoint_serializer())
        old_model = ProtocolCheckingModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "compare_windows", "args": {}, "id": "saved_comparison"}]),
            broken_report(),
        ])
        with patch("apps.trading_assistant.agent.ReportRecovery", LegacyRecovery):
            with self.assertRaisesMessage(ValueError, "unanswered tool calls"):
                run_agent(turn, saver, model=old_model)
        snapshot_id = turn.snapshot_id
        model = ProtocolCheckingModel(responses=[AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": report_payload(), "id": "recovered"}])])
        report = run_agent(turn, saver, model=model)
        self.assertEqual(report["stance"], "wait")
        self.assertEqual(turn.snapshot_id, snapshot_id)
        self.assertEqual(turn.tool_executions.count(), 1)
        turn.refresh_from_db()
        self.assertEqual(turn.usage["format_retries"], 1)

    @override_settings(TRADING_ASSISTANT_API_KEY="test", SOURCE_PROXY_URL="")
    def test_schema_errors_and_invalid_only_responses_can_recover(self):
        responses = [
            AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": {"stance": "wait"}, "id": "incomplete"}]),
            AIMessage(content="", invalid_tool_calls=[{"name": "TradingReport", "id": "bad", "args": "<DSML>", "error": "invalid"}]),
        ]
        for invalid in responses:
            with self.subTest(response=invalid):
                turn = self.turn()
                model = ProtocolCheckingModel(responses=[invalid, AIMessage(content="", tool_calls=[{"name": "TradingReport", "args": report_payload(), "id": "valid"}])])
                result = run_agent(turn, InMemorySaver(serde=checkpoint_serializer()), model=model)
                mark_success(turn, result)
                turn.refresh_from_db()
                self.assertEqual(turn.usage["format_retries"], 1)
