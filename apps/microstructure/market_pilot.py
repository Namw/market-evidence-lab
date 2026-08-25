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
    symbol: str, blocks: list[FourHourBlock], index: int
) -> dict[str, object]:
    block = blocks[index]
    prior = blocks[index - 6 : index]
    start_oi = _latest_oi(symbol, block.start)
    end_oi = _latest_oi(symbol, block.end)
    start_funding = _latest_funding(symbol, block.start)
    end_funding = _latest_funding(symbol, block.end)
    start_dvol = _latest_dvol(block.start)
    end_dvol = _latest_dvol(block.end)
    minute_metrics = MarketMinute.objects.filter(
        symbol=symbol,
        minute_start__gte=block.start,
        minute_start__lt=block.end,
    ).aggregate(
        rows=Count("id"),
        quote_volume=Sum("quote_volume"),
        taker_buy=Sum("taker_buy_quote"),
        taker_sell=Sum("taker_sell_quote"),
        delta=Sum("delta_quote"),
        spread_bps=Avg("spread_bps_p95"),
        top5_imbalance=Avg("imbalance_top5_mean"),
        coverage=Avg("coverage_ratio"),
    )
    buy = minute_metrics["taker_buy"] or Decimal(0)
    sell = minute_metrics["taker_sell"] or Decimal(0)
    total = buy + sell
    return {
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
            "prior_24h_four_hour_median_usd": _float(
                median(item.quote_volume for item in prior), 2
            ),
            "ratio_to_prior_median": _float(
                block.quote_volume / median(item.quote_volume for item in prior), 3
            ),
            "kline_taker_buy_share_pct": _float(
                block.taker_buy_quote_volume / block.quote_volume * 100, 3
            ),
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
        "funding": {
            "start_rate": _float(start_funding.funding_rate if start_funding else None),
            "end_rate": _float(end_funding.funding_rate if end_funding else None),
        },
        "dvol": {
            "start": _float(start_dvol.close if start_dvol else None, 3),
            "end": _float(end_dvol.close if end_dvol else None, 3),
        },
        "microstructure": {
            "minute_rows": minute_metrics["rows"] or 0,
            "quote_volume_usd": _float(minute_metrics["quote_volume"], 2),
            "aggressive_buy_share_pct": _float(buy / total * 100, 3)
            if total
            else None,
            "delta_quote_usd": _float(minute_metrics["delta"], 2),
            "spread_bps_p95_mean": _float(minute_metrics["spread_bps"], 4),
            "top5_imbalance_mean": _float(minute_metrics["top5_imbalance"], 4),
            "book_coverage_mean": _float(minute_metrics["coverage"], 4),
        },
    }


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


def analyze_with_deepseek(inputs: list[dict[str, object]]) -> tuple[list[dict], dict]:
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
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"prompt_version": PROMPT_VERSION, "windows": chunk},
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
