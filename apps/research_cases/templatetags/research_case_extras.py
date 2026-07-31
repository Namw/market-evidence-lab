import json

from django import template


register = template.Library()


@register.filter
def json_pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
