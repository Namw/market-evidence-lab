from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.collection.source_network import source_proxy_url
from apps.market_data.models import (
    DeribitVolatilityIndexCandle,
    FundingRate,
    Kline,
    OpenInterest,
)
from apps.microstructure.models import MarketMinute, MarketPilotReport, MarketPilotRun
from apps.news_data.models import NewsRawRecord


PROMPT_VERSION = "market-four-hour-pilot-v1"
MICROSTRUCTURE_PROMPT_VERSION = "market-two-hour-microstructure-v1"
MECHANISMS = {
    "trend_expansion",
    "short_squeeze",
    "long_liquidation",
    "technical_rebound",
    "technical_pullback",
    "liquidity_jump",
    "mixed",
    "insufficient_evidence",
}


@dataclass(frozen=True, slots=True)
class FourHourBlock:
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    quote_volume: Decimal
    taker_buy_quote_volume: Decimal

    @property
    def return_pct(self) -> float:
        return float((self.close / self.open - 1) * 100)


def _float(value: object, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _pct_change(start: object, end: object) -> float | None:
    if start is None or end is None or Decimal(start) == 0:
        return None
    return _float((Decimal(end) / Decimal(start) - 1) * 100)


def build_four_hour_blocks(symbol: str) -> list[FourHourBlock]:
    rows = list(
        Kline.objects.filter(symbol=symbol, interval=Kline.Interval.ONE_HOUR)
        .order_by("open_time")
    )
    by_time = {row.open_time: row for row in rows}
    blocks: list[FourHourBlock] = []
    for row in rows:
        start = row.open_time.astimezone(UTC)
        if start.minute or start.second or start.hour % 4:
            continue
        bucket = [by_time.get(start + timedelta(hours=offset)) for offset in range(4)]
        if any(item is None for item in bucket):
            continue
        complete = [item for item in bucket if item is not None]
        blocks.append(
            FourHourBlock(
                start=start,
                end=start + timedelta(hours=4),
                open=complete[0].open,
                high=max(item.high for item in complete),
                low=min(item.low for item in complete),
                close=complete[-1].close,
                quote_volume=sum((item.quote_volume for item in complete), Decimal(0)),
                taker_buy_quote_volume=sum(
                    (item.taker_buy_quote_volume for item in complete), Decimal(0)
                ),
            )
        )
    return blocks


def select_representative_indices(
    blocks: list[FourHourBlock], *, threshold_pct: float = 2.0
) -> list[int]:
    """Select all eligible shocks plus one calm up and one calm down window."""
    eligible = list(range(6, max(len(blocks) - 6, 6)))
    extremes = [
        index for index in eligible if abs(blocks[index].return_pct) >= threshold_pct
    ]
    calm = [index for index in eligible if index not in extremes]
    calm_up = min(
        (index for index in calm if blocks[index].return_pct >= 0),
        key=lambda index: blocks[index].return_pct,
        default=None,
    )
    calm_down = min(
        (index for index in calm if blocks[index].return_pct < 0),
        key=lambda index: abs(blocks[index].return_pct),
        default=None,
    )
    selected = extremes + [index for index in (calm_up, calm_down) if index is not None]
    return sorted(set(selected))


def _latest_oi(symbol: str, at: datetime) -> OpenInterest | None:
    for period in (OpenInterest.Period.FIVE_MINUTES, OpenInterest.Period.ONE_HOUR):
        row = (
            OpenInterest.objects.filter(
                symbol=symbol,
                period=period,
                timestamp__lte=at,
            )
            .order_by("-timestamp")
            .first()
        )
        if row is not None:
            return row
    return None


def _latest_funding(symbol: str, at: datetime) -> FundingRate | None:
    return (
        FundingRate.objects.filter(symbol=symbol, funding_time__lte=at)
        .order_by("-funding_time")
        .first()
    )


def _latest_dvol(at: datetime) -> DeribitVolatilityIndexCandle | None:
    return (
        DeribitVolatilityIndexCandle.objects.filter(open_time__lte=at)
        .order_by("-open_time")
        .first()
    )


def _market_evidence(
    symbol: str,
    blocks: list[FourHourBlock],
    index: int,
    *,
    baseline_windows: int = 6,
    include_contextual_evidence: bool = True,
) -> dict[str, object]:
    block = blocks[index]
    prior = blocks[max(0, index - baseline_windows) : index]
    start_oi = _latest_oi(symbol, block.start)
    end_oi = _latest_oi(symbol, block.end)
    minute_rows = MarketMinute.objects.filter(
        symbol=symbol,
        minute_start__gte=block.start,
        minute_start__lt=block.end,
    ).order_by("minute_start")
    minute_metrics = minute_rows.aggregate(
        rows=Count("id"),
        quote_volume=Sum("quote_volume"),
        taker_buy=Sum("taker_buy_quote"),
        taker_sell=Sum("taker_sell_quote"),
        delta=Sum("delta_quote"),
        spread_bps=Avg("spread_bps_p95"),
        top5_imbalance=Avg("imbalance_top5_mean"),
        coverage=Avg("coverage_ratio"),
        bid_depth=Avg("bid_depth_mean"),
        ask_depth=Avg("ask_depth_mean"),
    )
    prior_start = prior[0].start if prior else block.start
    prior_micro = MarketMinute.objects.filter(
        symbol=symbol,
        minute_start__gte=prior_start,
        minute_start__lt=block.start,
    ).aggregate(
        spread_bps=Avg("spread_bps_p95"),
        top5_imbalance=Avg("imbalance_top5_mean"),
        coverage=Avg("coverage_ratio"),
    )
    first_minute = minute_rows.first()
    last_minute = minute_rows.last()
    buy = minute_metrics["taker_buy"] or Decimal(0)
    sell = minute_metrics["taker_sell"] or Decimal(0)
    total = buy + sell
    prior_volume_median = median(item.quote_volume for item in prior) if prior else None
    start_depth = None
    end_depth = None
    if (
        first_minute is not None
        and first_minute.bid_depth_open is not None
        and first_minute.ask_depth_open is not None
    ):
        start_depth = first_minute.bid_depth_open + first_minute.ask_depth_open
    if (
        last_minute is not None
        and last_minute.bid_depth_close is not None
        and last_minute.ask_depth_close is not None
    ):
        end_depth = last_minute.bid_depth_close + last_minute.ask_depth_close
    spread_value = minute_metrics["spread_bps"]
    prior_spread = prior_micro["spread_bps"]
    evidence = {
        "price": {
            "open": _float(block.open, 2),
            "high": _float(block.high, 2),
            "low": _float(block.low, 2),
            "close": _float(block.close, 2),
            "return_pct": _float(block.return_pct, 3),
            "range_pct": _pct_change(block.low, block.high),
        },
        "volume": {
            "quote_volume_usd": _float(block.quote_volume, 2),
            "prior_24h_window_median_usd": _float(prior_volume_median, 2),
            "ratio_to_prior_median": _float(
                block.quote_volume / prior_volume_median, 3
            ) if prior_volume_median else None,
            "kline_taker_buy_share_pct": _float(
                block.taker_buy_quote_volume / block.quote_volume * 100, 3
            ) if block.quote_volume else None,
        },
        "open_interest": {
            "start_usd": _float(
                start_oi.sum_open_interest_value if start_oi else None, 2
            ),
            "end_usd": _float(end_oi.sum_open_interest_value if end_oi else None, 2),
            "change_pct": _pct_change(
                start_oi.sum_open_interest_value if start_oi else None,
                end_oi.sum_open_interest_value if end_oi else None,
            ),
        },
        "microstructure": {
            "minute_rows": minute_metrics["rows"] or 0,
            "quote_volume_usd": _float(minute_metrics["quote_volume"], 2),
            "aggressive_buy_share_pct": _float(buy / total * 100, 3)
            if total
            else None,
            "delta_quote_usd": _float(minute_metrics["delta"], 2),
            "spread_bps_p95_mean": _float(minute_metrics["spread_bps"], 4),
            "spread_ratio_to_prior_24h": _float(
                Decimal(spread_value) / Decimal(prior_spread), 3
            ) if spread_value is not None and prior_spread else None,
            "top5_imbalance_mean": _float(minute_metrics["top5_imbalance"], 4),
            "prior_24h_top5_imbalance_mean": _float(
                prior_micro["top5_imbalance"], 4
            ),
            "book_coverage_mean": _float(minute_metrics["coverage"], 4),
            "prior_24h_book_coverage_mean": _float(prior_micro["coverage"], 4),
            "bid_depth_mean_usd": _float(minute_metrics["bid_depth"], 2),
            "ask_depth_mean_usd": _float(minute_metrics["ask_depth"], 2),
            "top20_depth_start_usd": _float(start_depth, 2),
            "top20_depth_end_usd": _float(end_depth, 2),
            "top20_depth_change_pct": _pct_change(start_depth, end_depth),
        },
    }
    if include_contextual_evidence:
        start_funding = _latest_funding(symbol, block.start)
        end_funding = _latest_funding(symbol, block.end)
        start_dvol = _latest_dvol(block.start)
        end_dvol = _latest_dvol(block.end)
        evidence["funding"] = {
            "start_rate": _float(start_funding.funding_rate if start_funding else None),
            "end_rate": _float(end_funding.funding_rate if end_funding else None),
        }
        evidence["dvol"] = {
            "start": _float(start_dvol.close if start_dvol else None, 3),
            "end": _float(end_dvol.close if end_dvol else None, 3),
        }
    return evidence


def _news_evidence(end: datetime, *, limit: int = 8) -> list[dict[str, object]]:
    rows = list(
        NewsRawRecord.objects.filter(
            published_at__gte=end - timedelta(hours=24),
            published_at__lt=end,
        )
        .select_related("source")
        .order_by("-published_at")[:limit]
    )
    rows.reverse()
    return [
        {
            "news_id": row.id,
            "published_at": row.published_at.isoformat(),
            "source": row.source.code,
            "authority": row.source.authority_level,
            "scope": row.source.observation_scope,
            "title": row.title[:300],
            "summary": row.summary[:300],
        }
        for row in rows
    ]


def build_pilot_inputs(symbol: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    blocks = build_four_hour_blocks(symbol)
    selected = select_representative_indices(blocks)
    inputs: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    for index in selected:
        block = blocks[index]
        evidence = _market_evidence(symbol, blocks, index)
        inputs.append(
            {
                "window_start": block.start.isoformat(),
                "window_end": block.end.isoformat(),
                "selection_reason": "absolute_return_ge_2pct"
                if abs(block.return_pct) >= 2
                else "calm_control",
                "market_evidence": evidence,
                "news_available_before_window_end": _news_evidence(block.end),
                "known_data_limitations": [
                    "数据库仅包含ETHUSDT，不能独立验证BTC和全市场同步性。",
                    "ETF资金流与地址余额当前为空。",
                    "新闻可能缺少宏观来源；未出现的新闻不能推断为没有外部事件。",
                ],
            }
        )
        result: dict[str, object] = {
            "window_start": block.start.isoformat(),
            "window_end": block.end.isoformat(),
        }
        for horizon, offset in ((4, 1), (12, 3), (24, 6)):
            target = blocks[index + offset]
            result[f"future_{horizon}h_return_pct"] = _pct_change(
                block.close, target.close
            )
        outcomes.append(result)
    return inputs, outcomes


SYSTEM_PROMPT = """你是 Market Evidence Lab 的四小时市场预演分析员。你只能使用输入中在 window_end 之前已知的数据，不得补充外部知识，不得把未来结果、事后报道或相关性说成确定因果。

任务是区分：触发背景、市场放大机制、趋势顺延条件、技术调整条件。证据不足时必须选择 insufficient_evidence。不能提供买卖建议或价格预测。

每个窗口必须返回一次，JSON格式固定为：
{"analyses":[{"window_start":"ISO时间","mechanism":"trend_expansion|short_squeeze|long_liquidation|technical_rebound|technical_pullback|liquidity_jump|mixed|insufficient_evidence","confidence":"low|medium|high","trigger_assessment":"中文，不超过180字","amplifier_assessment":"中文，不超过180字","supporting_evidence":["最多4条具体证据"],"contrary_evidence":["最多3条反证或冲突"],"continuation_conditions":["未来需要验证的条件，最多3条"],"adjustment_conditions":["未来需要验证的条件，最多3条"],"limitations":["最多3条"]}]}

新闻只有在发布时间早于 window_end 且内容明确时才能作为证据。OI上升不能自动解释为空头逼空；价格上涨且OI下降才更符合存量空头回补，但仍需谨慎。资金费率在上涨之后升高通常是放大或拥挤结果，不是初始触发。"""

MICROSTRUCTURE_SYSTEM_PROMPT = """你是 Market Evidence Lab 的 ZECUSDT 两小时微观结构分析员。你只能使用输入中在 window_end 之前已知的价格、成交、主动买卖、Delta、Top20盘口深度、Top5盘口失衡、Spread P95、盘口覆盖率和5分钟OI，不得使用新闻、宏观事件、项目方消息、资金费率、DVOL或任何外部知识。

你的任务不是解释外部触发原因，而是判断市场内部的形成与放大机制、趋势顺延条件和技术调整条件。证据只能支持“更符合”某种机制，不能证明因果；证据不足或数据互相冲突时必须选择 insufficient_evidence。不能提供买卖建议、仓位建议或价格预测。

每个窗口必须返回一次，JSON格式固定为：
{"analyses":[{"window_start":"ISO时间","mechanism":"trend_expansion|short_squeeze|long_liquidation|technical_rebound|technical_pullback|liquidity_jump|mixed|insufficient_evidence","confidence":"low|medium|high","trigger_assessment":"中文，不超过180字；只描述窗口开始时可见的内部市场状态，不得虚构外因","amplifier_assessment":"中文，不超过180字","supporting_evidence":["最多4条具体证据"],"contrary_evidence":["最多3条反证或冲突"],"continuation_conditions":["未来需要验证的条件，最多3条"],"adjustment_conditions":["未来需要验证的条件，最多3条"],"limitations":["最多3条"]}]}

解释规则：价格上涨且OI下降更符合存量空头回补；价格下跌且OI下降更符合多头去杠杆，但都必须结合主动成交验证。价格与OI同向增长只表示新仓参与增加，不能确定新仓方向。Spread P95扩大、Top20深度下降只能说明流动性恶化或挂单变化，不能把撤单说成成交。Top5失衡是快照状态，不能单独证明后续方向。"""


def _parse_ai_content(content: object, expected_starts: set[str]) -> list[dict]:
    if not isinstance(content, str):
        raise ValueError("DeepSeek response content is not text")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    payload = json.loads(cleaned)
    analyses = payload.get("analyses") if isinstance(payload, dict) else None
    if not isinstance(analyses, list):
        raise ValueError("DeepSeek response lacks analyses")
    returned = {item.get("window_start") for item in analyses if isinstance(item, dict)}
    if returned != expected_starts:
        raise ValueError("DeepSeek response window set does not match the request")
    for item in analyses:
        if item.get("mechanism") not in MECHANISMS:
            raise ValueError("DeepSeek returned an unsupported mechanism")
        if item.get("confidence") not in {"low", "medium", "high"}:
            raise ValueError("DeepSeek returned an unsupported confidence")
    return analyses


def _analyze_with_deepseek(
    inputs: list[dict[str, object]],
    *,
    system_prompt: str,
    prompt_version: str,
) -> tuple[list[dict], dict]:
    if not settings.NEWS_AI_API_KEY:
        raise RuntimeError("NEWS_AI_API_KEY is not configured")
    analyses: list[dict] = []
    actual_models: set[str] = set()
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    with httpx.Client(
        timeout=settings.NEWS_AI_TIMEOUT_SECONDS,
        proxy=source_proxy_url("deepseek") or None,
        trust_env=False,
    ) as client:
        for offset in range(0, len(inputs), 2):
            chunk = inputs[offset : offset + 2]
            request_payload = {
                "model": settings.NEWS_AI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"prompt_version": prompt_version, "windows": chunk},
                            ensure_ascii=False,
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
            response = client.post(
                f"{settings.NEWS_AI_BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.NEWS_AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("DeepSeek response lacks choices")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise ValueError("DeepSeek response lacks a message")
            analyses.extend(
                _parse_ai_content(
                    message.get("content"),
                    {str(item["window_start"]) for item in chunk},
                )
            )
            actual_model = payload.get("model")
            if isinstance(actual_model, str) and actual_model:
                actual_models.add(actual_model)
            response_usage = payload.get("usage")
            if isinstance(response_usage, dict):
                for key in usage:
                    value = response_usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[key] += max(value, 0)
    return analyses, {
        "configured_model": settings.NEWS_AI_MODEL,
        "actual_models": sorted(actual_models),
        "request_count": (len(inputs) + 1) // 2,
        "usage": usage,
    }


def analyze_with_deepseek(inputs: list[dict[str, object]]) -> tuple[list[dict], dict]:
    return _analyze_with_deepseek(
        inputs,
        system_prompt=SYSTEM_PROMPT,
        prompt_version=PROMPT_VERSION,
    )


def analyze_microstructure_with_deepseek(
    inputs: list[dict[str, object]],
) -> tuple[list[dict], dict]:
    return _analyze_with_deepseek(
        inputs,
        system_prompt=MICROSTRUCTURE_SYSTEM_PROMPT,
        prompt_version=MICROSTRUCTURE_PROMPT_VERSION,
    )


def run_market_pilot(symbol: str) -> dict[str, object]:
    started_at = timezone.now()
    run = MarketPilotRun.objects.create(
        symbol=symbol,
        prompt_version=PROMPT_VERSION,
        configured_model=settings.NEWS_AI_MODEL,
        mode=MarketPilotRun.Mode.HISTORICAL,
        trigger=MarketPilotRun.Trigger.MANUAL,
        started_at=started_at,
    )
    try:
        inputs, outcomes = build_pilot_inputs(symbol)
        analyses, ai_metadata = analyze_with_deepseek(inputs)
        by_start = {item["window_start"]: item for item in analyses}
        outcome_by_start = {item["window_start"]: item for item in outcomes}
        reports = []
        report_models = []
        for item in inputs:
            start = str(item["window_start"])
            analysis = by_start[start]
            outcome = outcome_by_start[start]
            reports.append(
                {
                    "input_snapshot": item,
                    "ai_analysis": analysis,
                    "revealed_after_analysis": outcome,
                }
            )
            report_models.append(
                {
                    "symbol": symbol,
                    "window_start": datetime.fromisoformat(start),
                    "defaults": {
                        "run": run,
                        "window_end": datetime.fromisoformat(str(item["window_end"])),
                        "selection_reason": str(item["selection_reason"]),
                        "mechanism": str(analysis["mechanism"]),
                        "confidence": str(analysis["confidence"]),
                        "input_snapshot": item,
                        "ai_analysis": analysis,
                        "future_outcomes": outcome,
                        "status": MarketPilotReport.Status.COMPLETED,
                    },
                }
            )
        usage = ai_metadata["usage"]
        finished_at = timezone.now()
        with transaction.atomic():
            for report_values in report_models:
                MarketPilotReport.objects.update_or_create(**report_values)
            run.status = MarketPilotRun.Status.SUCCESS
            run.actual_models = ai_metadata["actual_models"]
            run.window_count = len(reports)
            run.request_count = int(ai_metadata["request_count"])
            run.input_tokens = int(usage["prompt_tokens"])
            run.output_tokens = int(usage["completion_tokens"])
            run.total_tokens = int(usage["total_tokens"])
            run.finished_at = finished_at
            run.save(
                update_fields=[
                    "status",
                    "actual_models",
                    "window_count",
                    "request_count",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "finished_at",
                ]
            )
    except Exception as exc:
        run.status = MarketPilotRun.Status.FAILED
        run.safe_error_summary = str(exc)[:500]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "safe_error_summary", "finished_at"])
        raise
    return {
        "run_id": run.pk,
        "pilot_version": PROMPT_VERSION,
        "generated_at": run.finished_at.isoformat(),
        "symbol": symbol,
        "window_count": len(reports),
        "future_outcomes_were_excluded_from_ai_input": True,
        "ai": ai_metadata,
        "reports": reports,
    }
