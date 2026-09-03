"""Bounded, read-only market queries and reproducible snapshot calculations."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.microstructure.models import MarketMinute
from .models import MarketSnapshot

CALCULATION_VERSION = "micro-entry-v1"
MAX_MINUTES = 1440
HORIZONS = (240, 480, 1440)


def local_iso(value):
    return value.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


NUMBER_FIELDS = (
    "open_price", "high_price", "low_price", "close_price", "quote_volume",
    "taker_buy_quote", "taker_sell_quote", "delta_quote", "bid_depth_mean",
    "ask_depth_mean", "imbalance_top5_mean", "spread_bps_mean", "spread_bps_p95",
    "coverage_ratio",
)


def number(value):
    if value is None:
        return None
    result = float(value)
    return round(result, 8) if math.isfinite(result) else None


def stamp(row):
    return datetime.fromisoformat(row["minute_start"])


def valid_price(row):
    values = [row.get(key) for key in ("open_price", "high_price", "low_price", "close_price")]
    if any(value is None or value <= 0 for value in values):
        return False
    o, h, l, c = values
    return l <= min(o, c) <= max(o, c) <= h


def valid_flow(row):
    values = [row.get(key) for key in ("quote_volume", "taker_buy_quote", "taker_sell_quote")]
    if any(value is None or value < 0 for value in values):
        return False
    total, buy, sell = values
    return abs(total - buy - sell) <= max(0.00001, total * 0.000001)


def valid_book(row):
    return (
        (row.get("coverage_ratio") or 0) >= 0.8
        and (row.get("bid_depth_mean") or 0) > 0
        and (row.get("ask_depth_mean") or 0) > 0
        and row.get("imbalance_top5_mean") is not None
        and -1 <= row["imbalance_top5_mean"] <= 1
        and row.get("spread_bps_mean") is not None
        and row["spread_bps_mean"] >= 0
        and row.get("spread_bps_p95") is not None
        and row["spread_bps_p95"] >= 0
    )


def window(snapshot, minutes, offset=0):
    if type(minutes) is not int or type(offset) is not int or not 1 <= minutes <= MAX_MINUTES or not 0 <= offset <= MAX_MINUTES - minutes:
        raise ValueError("查询窗口需在快照最近 1440 分钟内。")
    end = snapshot.cutoff - timedelta(minutes=offset)
    start = end - timedelta(minutes=minutes)
    return [row for row in snapshot.rows if start <= stamp(row) < end], start, end


def summarize(rows, start, end):
    expected = int((end - start).total_seconds() // 60)
    prices = [row for row in rows if valid_price(row)]
    flows = [row for row in rows if valid_flow(row)]
    books = [row for row in rows if valid_book(row)]
    buy = sum(row["taker_buy_quote"] for row in flows)
    sell = sum(row["taker_sell_quote"] for row in flows)
    total = buy + sell
    result = {
        "start": local_iso(start), "end_exclusive": local_iso(end),
        "expected_minutes": expected, "observed_minutes": len(rows),
        "valid_price_minutes": len(prices), "valid_flow_minutes": len(flows),
        "valid_book_minutes": len(books),
        "price_coverage": number(len(prices) / expected) if expected else 0,
        "book_sampling_coverage": number(sum(min(1, max(0, row.get("coverage_ratio") or 0)) for row in rows) / expected) if expected else 0,
        "quote_volume_usdt": number(total), "taker_buy_usdt": number(buy),
        "taker_sell_usdt": number(sell), "delta_usdt": number(buy - sell),
        "buy_share_pct": number(buy / total * 100) if total else None,
        "flow_imbalance": number((buy - sell) / total) if total else None,
    }
    if prices:
        first, last = prices[0], prices[-1]
        result.update({
            "first_price_at": first["minute_start"], "last_price_at": last["minute_start"],
            "open": first["open_price"], "close": last["close_price"],
            "high": max(row["high_price"] for row in prices),
            "low": min(row["low_price"] for row in prices),
            "return_pct": number((last["close_price"] / first["open_price"] - 1) * 100),
        })
    for source, target in (
        ("bid_depth_mean", "mean_top20_bid_depth_usdt"),
        ("ask_depth_mean", "mean_top20_ask_depth_usdt"),
        ("imbalance_top5_mean", "mean_top5_imbalance"),
        ("spread_bps_mean", "mean_spread_bps"),
        ("spread_bps_p95", "mean_minute_spread_p95_bps"),
    ):
        values = [row[source] for row in books if row.get(source) is not None]
        result[target] = number(mean(values)) if values else None
        result[target + "_samples"] = len(values)
    return result


def summary(snapshot, minutes, offset=0):
    return summarize(*window(snapshot, minutes, offset))


def assess_quality(snapshot):
    recent = summary(snapshot, 120)
    last = next((row for row in reversed(snapshot.rows) if valid_price(row)), None)
    age = (snapshot.cutoff - stamp(last) - timedelta(minutes=1)).total_seconds() if last else None
    last_book = next((row for row in reversed(snapshot.rows) if valid_book(row)), None)
    book_age = (snapshot.cutoff - stamp(last_book) - timedelta(minutes=1)).total_seconds() if last_book else None
    reasons = []
    if last is None:
        reasons.append("快照内没有有效的已收盘分钟价格。")
    elif age > 120:
        reasons.append("最新已收盘价格落后分析时间超过 2 分钟。")
    if last_book is None or book_age > 120:
        reasons.append("最新有效盘口已超过 2 分钟或不可用。")
    if recent["valid_price_minutes"] < 114:
        reasons.append("最近 120 分钟有效价格覆盖不足 95%。")
    if recent["valid_flow_minutes"] < 114:
        reasons.append("最近 120 分钟有效主动成交覆盖不足 95%。")
    if recent["book_sampling_coverage"] < 0.8 or recent["valid_book_minutes"] < 96:
        reasons.append("最近 120 分钟盘口采样覆盖不足。")
    for key in ("mean_top20_bid_depth_usdt", "mean_top20_ask_depth_usdt", "mean_top5_imbalance", "mean_spread_bps"):
        if recent[key + "_samples"] < 96:
            reasons.append("盘口关键指标有效分钟不足 80%。")
            break
    return {
        "usable_for_entry": not reasons, "reasons": reasons,
        "reference_price": last["close_price"] if last else None,
        "latest_minute_start": last["minute_start"] if last else None,
        "lag_seconds_at_capture": age,
        "book_lag_seconds_at_capture": book_age,
        "recent_120m": recent,
        "win_rate": None, "win_rate_note": "暂无可靠估计：尚未验证同一入场、退出和持有周期的历史策略。",
    }


def capture_snapshot(symbol, *, now=None):
    cutoff = (now or timezone.now()).replace(second=0, microsecond=0)
    # Explicit field allowlist excludes future returns and all post-cutoff labels.
    query = MarketMinute.objects.filter(
        symbol=symbol, kline_closed=True,
        minute_start__gte=cutoff - timedelta(minutes=MAX_MINUTES),
        minute_start__lt=cutoff, minute_end__lte=cutoff,
    ).order_by("minute_start").values("minute_start", *NUMBER_FIELDS)[:MAX_MINUTES]
    rows = []
    for source in query:
        row = {key: number(source[key]) for key in NUMBER_FIELDS}
        row["minute_start"] = local_iso(source["minute_start"])
        rows.append(row)
    snapshot = MarketSnapshot(symbol=symbol, cutoff=cutoff, rows=rows, calculation_version=CALCULATION_VERSION)
    snapshot.quality = assess_quality(snapshot)
    snapshot.save()
    return snapshot


def baseline(snapshot):
    return {
        "evidence_id": "E0", "symbol": snapshot.symbol,
        "cutoff": local_iso(snapshot.cutoff), "quality": snapshot.quality,
        "windows": {str(minutes): summary(snapshot, minutes) for minutes in (15, 60, 120)},
        "calculation_version": snapshot.calculation_version,
    }


def series(snapshot, minutes, bucket_minutes):
    if bucket_minutes not in (1, 5, 15, 30, 60) or minutes % bucket_minutes or minutes // bucket_minutes > 120:
        raise ValueError("周期需为 1/5/15/30/60 分钟、整除窗口，且最多返回 120 段。")
    rows, start, end = window(snapshot, minutes)
    buckets = []
    for index in range(minutes // bucket_minutes):
        left = start + timedelta(minutes=index * bucket_minutes)
        right = left + timedelta(minutes=bucket_minutes)
        buckets.append(summarize([row for row in rows if left <= stamp(row) < right], left, right))
    return {"bucket_minutes": bucket_minutes, "buckets": buckets}


def compare(snapshot, recent_minutes, previous_minutes):
    recent = summary(snapshot, recent_minutes)
    previous = summary(snapshot, previous_minutes, recent_minutes)
    recent_rate = recent["quote_volume_usdt"] / recent["valid_flow_minutes"] if recent["valid_flow_minutes"] else None
    previous_rate = previous["quote_volume_usdt"] / previous["valid_flow_minutes"] if previous["valid_flow_minutes"] else None
    return {
        "recent": recent, "previous": previous,
        "volume_per_observed_minute_ratio": number(recent_rate / previous_rate) if recent_rate is not None and previous_rate else None,
        "buy_share_change_percentage_points": number(recent["buy_share_pct"] - previous["buy_share_pct"]) if recent["buy_share_pct"] is not None and previous["buy_share_pct"] is not None else None,
        "note": "成交强度按有效分钟平均成交额比较；有缺口时不代表连续完整行情。",
    }


def trade_plan(snapshot, *, direction, horizon_minutes, entry_price=None, now=None):
    if direction not in ("long", "short") or horizon_minutes not in HORIZONS:
        raise ValueError("方向或持有周期不在支持范围内。")
    if not snapshot.quality["usable_for_entry"]:
        return {"available": False, "reason": "数据质量不足，不能生成开仓价格方案。"}
    if ((now or timezone.now()) - snapshot.cutoff).total_seconds() > 300:
        return {"available": False, "reason": "数据快照已超过 5 分钟，请更新行情后再生成价格方案。"}
    bucket = 5 if horizon_minutes <= 60 else 15
    rows, start, end = window(snapshot, bucket * 15)
    if len(rows) != bucket * 15 or any(not valid_price(row) for row in rows):
        return {"available": False, "reason": "波动计算需要连续完整的 15 根聚合 K 线。"}
    bars = []
    for index in range(15):
        chunk = rows[index * bucket:(index + 1) * bucket]
        bars.append((max(row["high_price"] for row in chunk), min(row["low_price"] for row in chunk), chunk[-1]["close_price"]))
    atr = mean(max(high - low, abs(high - bars[i - 1][2]), abs(low - bars[i - 1][2])) for i, (high, low, _) in enumerate(bars) if i)
    reference = snapshot.quality["reference_price"]
    entry = reference if entry_price is None else float(entry_price)
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError("入场价必须是有限正数。")
    if abs(entry / reference - 1) > 0.1:
        return {"available": False, "reason": "指定入场价偏离参考价超过 10%，当前局部数据不足以支持该假设。"}
    if atr <= 0:
        return {"available": False, "reason": "波动幅度为零，无法形成有效价格区间。"}
    levels = summary(snapshot, 60)
    low, high = levels["low"], levels["high"]
    if direction == "long":
        anchor = min(low, entry - atr)
        stop = [anchor - 0.5 * atr, anchor - 0.2 * atr]
        target_anchor = high if high > entry + 0.5 * atr else entry + 2 * atr
        target = [max(entry + 0.25 * atr, target_anchor - 0.2 * atr), target_anchor + 0.2 * atr]
        target_basis = "最近 60 分钟局部高点" if high > entry + 0.5 * atr else "高于现有区间，按 2×ATR 外推；不是已验证阻力位"
    else:
        anchor = max(high, entry + atr)
        stop = [anchor + 0.2 * atr, anchor + 0.5 * atr]
        target_anchor = low if low < entry - 0.5 * atr else entry - 2 * atr
        target = [target_anchor - 0.2 * atr, min(entry - 0.25 * atr, target_anchor + 0.2 * atr)]
        target_basis = "最近 60 分钟局部低点" if low < entry - 0.5 * atr else "低于现有区间，按 2×ATR 外推；不是已验证支撑位"
    if min(*stop, *target) <= 0:
        return {"available": False, "reason": "当前波动过大，候选价格区间无效。"}
    # Illustrative round-trip fees + slippage, explicitly disclosed, not an exchange quote.
    cost = entry * 12 / 10000
    risk = [abs(entry - value) + cost for value in stop]
    reward = [abs(value - entry) - cost for value in target]
    rr = [min(reward) / max(risk), max(reward) / min(risk)]
    return {
        "available": True, "direction": direction, "horizon_minutes": horizon_minutes,
        "entry_price": number(entry), "entry_basis": "最新已收盘分钟价，仅为现价参考" if entry_price is None else "用户或模型提出的限价情景，尚未成交",
        "reference_price": reference, "stop_zone": [number(value) for value in stop],
        "take_profit_zone": [number(value) for value in target],
        "stop_basis": "最近 60 分钟局部低点与至少 1×ATR 距离，额外留 0.2–0.5×ATR 缓冲" if direction == "long" else "最近 60 分钟局部高点与至少 1×ATR 距离，额外留 0.2–0.5×ATR 缓冲",
        "target_basis": target_basis, "atr": number(atr), "atr_bar_minutes": bucket,
        "atr_method": "前 15 根聚合 K 线计算 14 个 True Range 的简单均值",
        "risk_reward_after_cost_range": [number(value) for value in rr],
        "cost_assumption": "示例成本：每边手续费 4 bps、滑点 2 bps，往返共 12 bps；未计资金费，不是账户实际费率。",
        "assessment": "候选方案收益风险比偏低，不宜仅据此开仓。" if rr[0] < 1.2 else "候选方案有一定收益风险空间，仍需核对方向证据和触发条件。",
        "expiry_note": "这是按所选持仓周期评估的价格情景，不保证期间触达；价位失效、行情变化或到期均应重评。止损触发价不保证成交价。",
        "win_rate": None,
    }
