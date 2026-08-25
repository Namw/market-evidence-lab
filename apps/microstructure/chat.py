"""Grounded answers for the microstructure page assistant.

The assistant deliberately derives every number from ``MarketMinute`` rather
than asking a language model to infer live market data.  This keeps market
answers reproducible and makes missing coverage visible to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from .models import MarketMinute


@dataclass(frozen=True)
class ChatReply:
    answer: str
    intent: str


def _format_number(value: Decimal | None, *, decimals: int = 2) -> str:
    if value is None:
        return "—"
    rendered = f"{value:,.{decimals}f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _format_price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    decimals = 4 if abs(value) < Decimal("100") else 2
    return _format_number(value, decimals=decimals)


def _window_hours(question: str, default: int) -> int:
    match = re.search(r"(?:近|最近|过去|过去的|last\s*)(\d+)\s*(?:个)?(?:小时|h)\b", question, re.I)
    if not match:
        return default
    hours = int(match.group(1))
    return max(1, min(hours, 24))


def _window(symbol: str, hours: int) -> tuple[list[MarketMinute], MarketMinute | None]:
    latest = (
        MarketMinute.objects.filter(symbol=symbol)
        .exclude(close_price__isnull=True)
        .order_by("-minute_start")
        .first()
    )
    if latest is None:
        return [], None
    start = latest.minute_start - timedelta(hours=hours)
    rows = list(
        MarketMinute.objects.filter(symbol=symbol, minute_start__gte=start, minute_start__lte=latest.minute_start)
        .order_by("minute_start")
    )
    return rows, latest


def _price_level(current: Decimal, low: Decimal, high: Decimal) -> tuple[str, Decimal | None]:
    if high <= low:
        return "区间持平，无法区分价格位置", None
    position = (current - low) / (high - low) * Decimal("100")
    if position >= Decimal("67"):
        label = "区间高位"
    elif position <= Decimal("33"):
        label = "区间低位"
    else:
        label = "区间中位"
    return label, position


def _coverage_label(rows: list[MarketMinute], hours: int) -> str:
    expected = hours * 60
    return f"数据覆盖 {len(rows)}/{expected} 分钟"


def _price_reply(symbol: str, question: str) -> ChatReply:
    hours = _window_hours(question, default=4)
    rows, latest = _window(symbol, hours)
    highs = [row.high_price for row in rows if row.high_price is not None]
    lows = [row.low_price for row in rows if row.low_price is not None]
    if latest is None or latest.close_price is None or not highs or not lows:
        return ChatReply(
            "目前还没有足够的分钟价格数据。请先启动采集，累计至少一根完整分钟 K 线后再查询。",
            "no_data",
        )
    high, low, current = max(highs), min(lows), latest.close_price
    first_open = next((row.open_price for row in rows if row.open_price is not None), None)
    amplitude = ((high - low) / first_open * Decimal("100")) if first_open and first_open > 0 else None
    level, position = _price_level(current, low, high)
    answer = (
        f"{symbol} 最近 {hours} 小时最高 {_format_price(high)}，最低 {_format_price(low)}，"
        f"当前价 {_format_price(current)}。当前位于这段区间的 {position:.1f}%（{level}）；"
        f"区间振幅为 {amplitude:.2f}%（以窗口首根开盘价计算）。\n\n"
        f"统计截至 {latest.minute_end:%Y-%m-%d %H:%M} UTC，{_coverage_label(rows, hours)}。"
    )
    return ChatReply(answer, "price_range")


def _flow_reply(symbol: str, question: str) -> ChatReply:
    hours = _window_hours(question, default=2)
    rows, latest = _window(symbol, hours)
    price_rows = [row for row in rows if row.close_price is not None]
    high_rows = [row for row in price_rows if row.high_price is not None]
    low_rows = [row for row in price_rows if row.low_price is not None]
    if latest is None or not high_rows or not low_rows:
        return ChatReply("目前还没有足够的分钟数据。请先启动采集后再查询主动成交。", "no_data")
    high = max(row.high_price for row in high_rows)
    low = min(row.low_price for row in low_rows)
    wants_buy = any(token in question.lower() for token in ("买", "buy"))
    wants_sell = any(token in question.lower() for token in ("卖", "sell"))
    if not wants_buy and not wants_sell:
        wants_buy = wants_sell = True

    lines: list[str] = []
    for label, field, requested in (
        ("主动买入", "taker_buy_quote", wants_buy),
        ("主动卖出", "taker_sell_quote", wants_sell),
    ):
        if not requested:
            continue
        eligible = [row for row in price_rows if getattr(row, field) is not None]
        if not eligible:
            lines.append(f"{label}：暂无可用数据")
            continue
        peak = max(eligible, key=lambda row: getattr(row, field) or Decimal(0))
        level, position = _price_level(peak.close_price, low, high)
        lines.append(
            f"{label}单分钟最高为 {_format_number(getattr(peak, field))} USDT，发生在 "
            f"{peak.minute_start:%H:%M} UTC；当时收盘价 {_format_price(peak.close_price)}，"
            f"处在这 {hours} 小时价格区间的 {position:.1f}%（{level}）。"
        )
    return ChatReply(
        f"{symbol} 最近 {hours} 小时：\n" + "\n".join(lines) +
        f"\n\n统计截至 {latest.minute_end:%Y-%m-%d %H:%M} UTC，{_coverage_label(rows, hours)}。",
        "aggressive_flow",
    )


def reply_to_question(*, symbol: str, question: str) -> ChatReply:
    normalized = " ".join(question.strip().split())
    if not normalized:
        return ChatReply("请输入一个关于当前交易对价格、振幅或主动成交的问题。", "empty")
    lower = normalized.lower()
    flow_tokens = ("主动", "成交", "买入", "卖出", "买", "卖", "buy", "sell", "delta")
    if any(token in lower for token in flow_tokens):
        return _flow_reply(symbol, normalized)
    price_tokens = ("价格", "最高", "最低", "波动", "振幅", "高低", "price", "high", "low", "volatility")
    if any(token in lower for token in price_tokens):
        return _price_reply(symbol, normalized)
    return ChatReply(
        "我目前只回答当前页面交易对的分钟级事实数据。你可以问：\n"
        "• 最近4小时最高、最低、当前价格和价格位置\n"
        "• 当前波动幅度如何\n"
        "• 最近2小时主动买入或卖出最高是多少、当时价格处于什么水平\n\n"
        "所有结果均基于本页已采集数据，不构成交易建议。",
        "unsupported",
    )
