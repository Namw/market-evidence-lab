from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.collection.models import SourceNetworkPolicy
from apps.collection.source_network import (
    BUILTIN_SOURCES,
    get_source_network_policy,
    safe_proxy_label,
)
from apps.news_data.models import NewsSource

def _source_network_rows() -> list[dict]:
    definitions = [
        {
            "key": item.key,
            "name": item.name,
            "category": item.category,
            "endpoint": item.endpoint,
            "note": item.note,
        }
        for item in BUILTIN_SOURCES
    ]
    definitions.extend(
        {
            "key": source.code,
            "name": source.name,
            "category": "新闻采集",
            "endpoint": urlsplit(source.base_url).netloc or source.base_url,
            "note": f"{source.feeds.filter(enabled=True).count()} 个启用栏目",
        }
        for source in NewsSource.objects.filter(enabled=True).order_by("name")
    )
    rows = []
    for definition in definitions:
        policy = get_source_network_policy(definition["key"])
        rows.append({**definition, "policy": policy})
    return rows


@require_http_methods(["GET", "POST"])
def source_network_settings(request):
    rows = _source_network_rows()
    if request.method == "POST":
        valid_keys = {row["key"] for row in rows}
        requested = {
            key: request.POST.get(f"route_{key}", "direct")
            for key in valid_keys
        }
        if any(value not in {"direct", "proxy"} for value in requested.values()):
            messages.error(request, "来源网络策略包含无法识别的连接方式。")
        elif "proxy" in requested.values() and not settings.SOURCE_PROXY_URL:
            messages.error(request, "请先在环境变量中配置 SOURCE_PROXY_URL。")
        else:
            with transaction.atomic():
                for source_key, route in requested.items():
                    SourceNetworkPolicy.objects.update_or_create(
                        source_key=source_key,
                        defaults={"use_proxy": route == "proxy"},
                    )
            messages.success(request, "来源网络策略已保存，后续调度将自动沿用。")
            return redirect("scheduling:sources")
        rows = _source_network_rows()
    return render(
        request,
        "scheduling/source_network.html",
        {
            "source_rows": rows,
            "proxy_configured": bool(settings.SOURCE_PROXY_URL),
            "proxy_label": safe_proxy_label(),
        },
    )

