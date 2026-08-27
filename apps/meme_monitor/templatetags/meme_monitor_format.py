from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def human_usd(value):
    if value is None:
        return "—"
    amount = Decimal(value)
    absolute = abs(amount)
    if absolute >= Decimal(1_000_000_000):
        return f"${amount / Decimal(1_000_000_000):,.2f}B"
    if absolute >= Decimal(1_000_000):
        return f"${amount / Decimal(1_000_000):,.2f}M"
    if absolute >= Decimal(1_000):
        return f"${amount / Decimal(1_000):,.2f}K"
    return f"${amount:,.2f}"


@register.filter
def price_usd(value):
    if value is None:
        return "—"
    amount = Decimal(value)
    if abs(amount) >= 1:
        return f"${amount:,.4f}"
    return f"${amount:,.12f}".rstrip("0").rstrip(".")


@register.filter
def signed_pct(value):
    if value is None:
        return "—"
    return f"{Decimal(value):+,.2f}%"


@register.filter
def duration_seconds(value):
    if value is None:
        return "—"
    seconds = max(0, int(value))
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    return f"{seconds // 3600} 小时 {seconds % 3600 // 60} 分钟"
