from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Callable

from django.db.models import QuerySet

from .calculations import decimal_18
from .models import MarketMinute

FUTURE_HORIZON_MINUTES = 5
TRADE_INTENSITY_LOOKBACK_MINUTES = 60
SPREAD_EXPANSION_LOOKBACK_MINUTES = 60
MIN_DEPTH_COVERAGE_RATIO = Decimal("0.80")
TRAIN_RATIO = Decimal("0.70")

RESEARCH_METRICS = {
    "trade_imbalance": {
        "key": "trade_imbalance",
        "short_name": "主动成交失衡",
        "title": "主动成交失衡预测研究",
        "description": "观察主动买卖成交额的相对失衡，能否稳定区分未来5分钟价格结果。",
        "formula": "(主动买入额 − 主动卖出额) / (主动买入额 + 主动卖出额)",
        "method_note": "数值范围为 −1 到 1；越低越偏主动卖出，越高越偏主动买入。",
        "axis_label": "成交失衡十分位（D1 主动卖出 → D10 主动买入）",
        "low_label": "强主动卖出",
        "high_label": "强主动买入",
        "range_places": 4,
        "range_suffix": "",
        "range_multiplier": Decimal(1),
    },
    "trade_intensity": {
        "key": "trade_intensity",
        "short_name": "成交强度",
        "title": "成交强度预测研究",
        "description": "观察当前1分钟成交额相对近期正常水平的放大程度，能否稳定区分未来5分钟价格结果。",
        "formula": "当前1分钟成交额 / 前60个连续完整分钟成交额中位数",
        "method_note": "基准只使用当前分钟之前的数据；前60分钟有缺口、未收盘或中位数为0时，指标为空。",
        "axis_label": "成交强度十分位（D1 低强度 → D10 高强度）",
        "low_label": "低成交强度",
        "high_label": "高成交强度",
        "range_places": 2,
        "range_suffix": "×",
        "range_multiplier": Decimal(1),
    },
    "depth_drop": {
        "key": "depth_drop",
        "short_name": "盘口深度减少",
        "title": "盘口深度快速减少预测研究",
        "description": "观察Top20买卖盘总深度在1分钟内快速减少，能否稳定区分未来5分钟价格结果。",
        "formula": "(分钟初Top20总深度 − 分钟末Top20总深度) / 分钟初Top20总深度",
        "method_note": "正值代表深度减少，负值代表深度增加；只使用盘口覆盖率不低于80%且至少有2次抽样的分钟。深度减少本身不能区分成交与撤单。",
        "axis_label": "盘口深度变化十分位（D1 深度增加 → D10 深度减少）",
        "low_label": "深度增加",
        "high_label": "深度减少",
        "range_places": 2,
        "range_suffix": "%",
        "range_multiplier": Decimal(100),
    },
    "spread_expansion": {
        "key": "spread_expansion",
        "short_name": "Spread 扩大",
        "title": "Spread 异常扩大预测研究",
        "description": "观察当前1分钟高位价差相对近期正常水平的扩大程度，能否稳定区分未来5分钟价格结果。",
        "formula": "当前1分钟 spread_bps_p95 / 前60个连续有效分钟 spread_bps_p95 中位数",
        "method_note": "基准只使用当前分钟之前的数据；前60分钟必须连续且盘口覆盖率不低于80%、至少有2次抽样。中位数为0时指标为空，数值大于1代表价差相对近期扩大。",
        "axis_label": "Spread扩大十分位（D1 相对收窄 → D10 相对扩大）",
        "low_label": "相对收窄",
        "high_label": "相对扩大",
        "range_places": 2,
        "range_suffix": "×",
        "range_multiplier": Decimal(1),
    },
    "top5_imbalance": {
        "key": "top5_imbalance",
        "short_name": "Top5盘口失衡",
        "title": "Top5盘口失衡预测研究",
        "description": "观察距离当前价格最近的Top5买卖盘深度失衡，能否稳定区分未来5分钟价格结果。",
        "formula": "(Top5买盘金额 − Top5卖盘金额) / (Top5买盘金额 + Top5卖盘金额)",
        "method_note": "使用1分钟内所有有效盘口快照的失衡均值；数值范围为 −1 到 1，越低代表近端卖盘越厚，越高代表近端买盘越厚。只使用Top5自身有效覆盖率不低于80%且至少有2次有效抽样的分钟。",
        "axis_label": "Top5盘口失衡十分位（D1 卖盘厚 → D10 买盘厚）",
        "low_label": "近端卖盘厚",
        "high_label": "近端买盘厚",
        "range_places": 4,
        "range_suffix": "",
        "range_multiplier": Decimal(1),
    },
}


@dataclass(frozen=True, slots=True)
class ResearchObservation:
    minute_start: datetime
    metric_value: Decimal
    future_5m_return: Decimal


def _future_return(
    current_close: Decimal,
    future_close: Decimal,
) -> Decimal | None:
    current_close = Decimal(current_close)
    future_close = Decimal(future_close)
    if current_close <= 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return decimal_18(future_close / current_close - Decimal(1))


def calculate_future_5m_returns(
    rows: list[MarketMinute],
) -> dict[int, Decimal | None]:
    """Calculate labels by timestamps; never bridge a missing/unclosed minute."""
    by_start = {row.minute_start: row for row in rows}
    result: dict[int, Decimal | None] = {}
    step = timedelta(minutes=1)
    horizon = timedelta(minutes=FUTURE_HORIZON_MINUTES)

    for row in rows:
        value = None
        window = [
            by_start.get(row.minute_start + offset * step)
            for offset in range(FUTURE_HORIZON_MINUTES + 1)
        ]
        if all(
            item is not None
            and item.kline_closed
            and item.close_price is not None
            for item in window
        ):
            target = by_start[row.minute_start + horizon]
            value = _future_return(row.close_price, target.close_price)
        result[row.pk] = value
    return result


def refresh_future_5m_returns(
    *,
    symbol: str,
    candidate_start: datetime | None = None,
    candidate_end: datetime | None = None,
) -> int:
    candidates: QuerySet[MarketMinute] = MarketMinute.objects.filter(
        symbol=symbol
    ).only(
        "minute_start",
        "close_price",
        "kline_closed",
        "future_5m_return",
    )
    if candidate_start is not None:
        candidates = candidates.filter(minute_start__gte=candidate_start)
    if candidate_end is not None:
        candidates = candidates.filter(minute_start__lte=candidate_end)
    candidate_rows = list(candidates.order_by("minute_start"))
    if not candidate_rows:
        return 0

    window_start = candidate_rows[0].minute_start
    window_end = candidate_rows[-1].minute_start + timedelta(
        minutes=FUTURE_HORIZON_MINUTES
    )
    rows = list(
        MarketMinute.objects.filter(
            symbol=symbol,
            minute_start__gte=window_start,
            minute_start__lte=window_end,
        )
        .only(
            "minute_start",
            "close_price",
            "kline_closed",
            "future_5m_return",
        )
        .order_by("minute_start")
    )
    labels = calculate_future_5m_returns(rows)
    changed: list[MarketMinute] = []
    candidate_ids = {row.pk for row in candidate_rows}
    for row in rows:
        if row.pk not in candidate_ids:
            continue
        value = labels[row.pk]
        if row.future_5m_return != value:
            row.future_5m_return = value
            changed.append(row)
    if changed:
        MarketMinute.objects.bulk_update(
            changed,
            ["future_5m_return"],
            batch_size=1_000,
        )
    return len(changed)


def trade_imbalance(row: MarketMinute) -> Decimal | None:
    total = row.taker_buy_quote + row.taker_sell_quote
    if total <= 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return decimal_18((row.taker_buy_quote - row.taker_sell_quote) / total)


def _calculate_prior_median_ratio(
    rows: list[MarketMinute],
    *,
    lookback: int,
    value_for_row: Callable[[MarketMinute], Decimal],
    is_valid_row: Callable[[MarketMinute], bool],
) -> dict[int, Decimal | None]:
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    result: dict[int, Decimal | None] = {}
    prior: list[Decimal] = []
    previous_start: datetime | None = None
    step = timedelta(minutes=1)

    for row in rows:
        current_is_valid = is_valid_row(row)
        if previous_start is None or row.minute_start != previous_start + step:
            prior = []

        value = None
        if current_is_valid and len(prior) == lookback:
            ordered = sorted(prior)
            middle = lookback // 2
            median = (
                ordered[middle]
                if lookback % 2
                else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
            )
            if median > 0:
                with localcontext() as context:
                    context.prec = 60
                    value = decimal_18(value_for_row(row) / median)
        result[row.pk] = value

        if current_is_valid:
            prior.append(value_for_row(row))
            if len(prior) > lookback:
                prior.pop(0)
        else:
            prior = []
        previous_start = row.minute_start
    return result


def calculate_trade_intensity(
    rows: list[MarketMinute],
    *,
    lookback: int = TRADE_INTENSITY_LOOKBACK_MINUTES,
) -> dict[int, Decimal | None]:
    """Compare volume with a strictly prior, gap-free rolling median."""
    return _calculate_prior_median_ratio(
        rows,
        lookback=lookback,
        value_for_row=lambda row: Decimal(row.quote_volume),
        is_valid_row=lambda row: (
            row.kline_closed
            and row.quote_volume is not None
            and Decimal(row.quote_volume) >= 0
        ),
    )


def calculate_spread_expansion(
    rows: list[MarketMinute],
    *,
    lookback: int = SPREAD_EXPANSION_LOOKBACK_MINUTES,
) -> dict[int, Decimal | None]:
    """Compare spread P95 with a strictly prior, gap-free quality window."""
    return _calculate_prior_median_ratio(
        rows,
        lookback=lookback,
        value_for_row=lambda row: Decimal(row.spread_bps_p95),
        is_valid_row=lambda row: (
            row.spread_bps_p95 is not None
            and Decimal(row.spread_bps_p95) >= 0
            and row.book_sample_count >= 2
            and Decimal(row.coverage_ratio) >= MIN_DEPTH_COVERAGE_RATIO
        ),
    )


def depth_drop_ratio(row: MarketMinute) -> Decimal | None:
    required = (
        row.bid_depth_open,
        row.ask_depth_open,
        row.bid_depth_close,
        row.ask_depth_close,
    )
    if any(value is None for value in required):
        return None
    if (
        row.book_sample_count < 2
        or Decimal(row.coverage_ratio) < MIN_DEPTH_COVERAGE_RATIO
    ):
        return None
    open_depth = Decimal(row.bid_depth_open) + Decimal(row.ask_depth_open)
    close_depth = Decimal(row.bid_depth_close) + Decimal(row.ask_depth_close)
    if open_depth <= 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return decimal_18((open_depth - close_depth) / open_depth)


def top5_imbalance(row: MarketMinute) -> Decimal | None:
    if row.imbalance_top5_mean is None:
        return None
    if (
        row.book_sample_count < 2
        or row.imbalance_top5_sample_count < 2
        or Decimal(row.coverage_ratio) < MIN_DEPTH_COVERAGE_RATIO
    ):
        return None
    with localcontext() as context:
        context.prec = 60
        top5_coverage = (
            Decimal(row.coverage_ratio)
            * Decimal(row.imbalance_top5_sample_count)
            / Decimal(row.book_sample_count)
        )
    if top5_coverage < MIN_DEPTH_COVERAGE_RATIO:
        return None
    value = Decimal(row.imbalance_top5_mean)
    if value < Decimal(-1) or value > Decimal(1):
        return None
    return value


def _nearest_rank_cutpoints(values: list[Decimal]) -> list[Decimal]:
    ordered = sorted(values)
    return [
        ordered[max(0, math.ceil(len(ordered) * percentile / 10) - 1)]
        for percentile in range(1, 10)
    ]


def _summary(
    observations: list[ResearchObservation],
) -> dict[str, object]:
    if not observations:
        return {
            "sample_count": 0,
            "mean_future_return": None,
            "up_ratio": None,
        }
    count = len(observations)
    with localcontext() as context:
        context.prec = 60
        mean_return = sum(
            (item.future_5m_return for item in observations), Decimal(0)
        ) / Decimal(count)
        up_ratio = Decimal(
            sum(item.future_5m_return > 0 for item in observations)
        ) / Decimal(count)
    return {
        "sample_count": count,
        "mean_future_return": decimal_18(mean_return),
        "up_ratio": decimal_18(up_ratio),
    }


def _bucket_summaries(
    observations: list[ResearchObservation],
    cutpoints: list[Decimal],
) -> list[dict[str, object]]:
    buckets: list[list[ResearchObservation]] = [[] for _ in range(10)]
    for item in observations:
        index = min(9, bisect_right(cutpoints, item.metric_value))
        buckets[index].append(item)
    return [_summary(bucket) for bucket in buckets]


def _readiness(sample_count: int) -> tuple[str, str]:
    if sample_count < 1_000:
        return "流程验证", "样本较少，仅用于检查计算是否符合预期。"
    if sample_count < 10_000:
        return "样本积累中", "可以观察分组形态，暂不适合下稳定结论。"
    if sample_count < 30_000:
        return "初步观察", "已可做探索分析，仍建议继续积累跨行情数据。"
    return "可做首轮验证", "样本规模已支持第一轮时间外验证，但仍不是交易结论。"


def build_decile_research(
    symbol: str,
    *,
    metric_key: str = "trade_imbalance",
) -> dict[str, object]:
    if metric_key not in RESEARCH_METRICS:
        raise ValueError(f"Unsupported research metric: {metric_key}")
    metric = RESEARCH_METRICS[metric_key]
    selected_fields = [
        "minute_start",
        "future_5m_return",
    ]
    if metric_key == "trade_imbalance":
        selected_fields.extend(["taker_buy_quote", "taker_sell_quote"])
    elif metric_key == "trade_intensity":
        selected_fields.extend(["quote_volume", "kline_closed"])
    elif metric_key == "depth_drop":
        selected_fields.extend(
            [
                "bid_depth_open",
                "ask_depth_open",
                "bid_depth_close",
                "ask_depth_close",
                "book_sample_count",
                "coverage_ratio",
            ]
        )
    elif metric_key == "spread_expansion":
        selected_fields.extend(
            [
                "spread_bps_p95",
                "book_sample_count",
                "coverage_ratio",
            ]
        )
    else:
        selected_fields.extend(
            [
                "imbalance_top5_mean",
                "imbalance_top5_sample_count",
                "book_sample_count",
                "coverage_ratio",
            ]
        )
    all_rows = list(
        MarketMinute.objects.filter(symbol=symbol)
        .only(*selected_fields)
        .order_by("minute_start")
    )
    metric_values = {}
    if metric_key == "trade_intensity":
        metric_values = calculate_trade_intensity(all_rows)
    elif metric_key == "spread_expansion":
        metric_values = calculate_spread_expansion(all_rows)
    observations: list[ResearchObservation] = []
    for row in all_rows:
        if metric_key == "trade_imbalance":
            metric_value = trade_imbalance(row)
        elif metric_key == "trade_intensity":
            metric_value = metric_values[row.pk]
        elif metric_key == "depth_drop":
            metric_value = depth_drop_ratio(row)
        elif metric_key == "spread_expansion":
            metric_value = metric_values[row.pk]
        else:
            metric_value = top5_imbalance(row)
        if row.future_5m_return is None or metric_value is None:
            continue
        observations.append(
            ResearchObservation(
                minute_start=row.minute_start,
                metric_value=metric_value,
                future_5m_return=row.future_5m_return,
            )
        )

    split_index = math.floor(len(observations) * float(TRAIN_RATIO))
    validation = observations[split_index:]
    validation_start = validation[0].minute_start if validation else None
    discovery_candidates = observations[:split_index]
    discovery = [
        item
        for item in discovery_candidates
        if validation_start is None
        or item.minute_start + timedelta(minutes=FUTURE_HORIZON_MINUTES)
        < validation_start
    ]
    purged_count = len(discovery_candidates) - len(discovery)

    cutpoints = (
        _nearest_rank_cutpoints([item.metric_value for item in discovery])
        if discovery
        else []
    )
    discovery_groups = _bucket_summaries(discovery, cutpoints) if cutpoints else []
    validation_groups = _bucket_summaries(validation, cutpoints) if cutpoints else []
    groups: list[dict[str, object]] = []
    for index in range(10):
        lower = None if index == 0 or not cutpoints else cutpoints[index - 1]
        upper = None if index == 9 or not cutpoints else cutpoints[index]
        groups.append(
            {
                "decile": index + 1,
                "lower": lower,
                "upper": upper,
                "discovery": discovery_groups[index] if discovery_groups else _summary([]),
                "validation": validation_groups[index] if validation_groups else _summary([]),
            }
        )

    status_label, status_detail = _readiness(len(observations))
    labeled_count = sum(row.future_5m_return is not None for row in all_rows)
    return {
        "symbol": symbol,
        "metric": metric,
        "minute_count": len(all_rows),
        "labeled_count": labeled_count,
        "sample_count": len(observations),
        "excluded_count": len(all_rows) - len(observations),
        "discovery_count": len(discovery),
        "validation_count": len(validation),
        "purged_count": purged_count,
        "range_start": all_rows[0].minute_start if all_rows else None,
        "range_end": all_rows[-1].minute_start if all_rows else None,
        "validation_start": validation_start,
        "status_label": status_label,
        "status_detail": status_detail,
        "groups": groups,
    }


def build_trade_imbalance_research(symbol: str) -> dict[str, object]:
    return build_decile_research(symbol, metric_key="trade_imbalance")


def build_trade_intensity_research(symbol: str) -> dict[str, object]:
    return build_decile_research(symbol, metric_key="trade_intensity")


def build_depth_drop_research(symbol: str) -> dict[str, object]:
    return build_decile_research(symbol, metric_key="depth_drop")


def build_spread_expansion_research(symbol: str) -> dict[str, object]:
    return build_decile_research(symbol, metric_key="spread_expansion")


def build_top5_imbalance_research(symbol: str) -> dict[str, object]:
    return build_decile_research(symbol, metric_key="top5_imbalance")
