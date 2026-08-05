from decimal import Decimal

from django import template


register = template.Library()


@register.filter
def human_usd(value):
    if value in (None, ""):
        return "暂无数据"
    try:
        value = Decimal(value)
    except Exception:
        return "暂无数据"
    absolute = abs(value)
    if absolute >= Decimal("1000000000"):
        return f"${value / Decimal('1000000000'):,.2f}B"
    if absolute >= Decimal("1000000"):
        return f"${value / Decimal('1000000'):,.2f}M"
    return f"${value:,.2f}"


@register.filter
def signed_usd(value):
    if value in (None, ""):
        return "未公布"
    try:
        value = Decimal(value)
    except Exception:
        return "未公布"
    prefix = "+" if value > 0 else ""
    return prefix + human_usd(value)


@register.filter
def signed_eth(value):
    if value in (None, ""):
        return "无可比快照"
    try:
        value = Decimal(value)
    except Exception:
        return "无可比快照"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:,.4f} ETH"


@register.filter
def flow_class(value):
    if value in (None, ""):
        return "is-missing"
    try:
        value = Decimal(value)
    except Exception:
        return "is-missing"
    return "is-positive" if value > 0 else "is-negative" if value < 0 else "is-zero"
