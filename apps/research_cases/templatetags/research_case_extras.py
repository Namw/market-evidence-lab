import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def json_pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def human_price(value):
    number = _decimal(value)
    if number is None:
        return "—"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


@register.filter
def human_pct(value):
    number = _decimal(value)
    if number is None:
        return "—"
    return f"{number:,.2f}"


@register.filter
def human_volume(value):
    number = _decimal(value)
    if number is None:
        return "—"
    return f"{number:,.2f}"


@register.filter
def human_funding(value):
    number = _decimal(value)
    if number is None:
        return "—"
    return f"{number * Decimal('100'):.6f}%"


@register.filter
def utc_hour(value):
    if not value:
        return "—"
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.strftime("%Y-%m-%d %H:%M UTC")
