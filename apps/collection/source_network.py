from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

from .models import SourceNetworkPolicy


DEFAULT_PROXY_SOURCE_KEYS = {
    "binance_announcements",
    "bls",
    "coindesk",
    "deribit",
    "sec",
}


@dataclass(frozen=True, slots=True)
class BuiltinSource:
    key: str
    name: str
    category: str
    endpoint: str
    note: str = ""


BUILTIN_SOURCES = (
    BuiltinSource(
        "binance_futures",
        "Binance Futures",
        "行情数据",
        "fapi.binance.com",
        "当前代理出口返回地区限制，切换代理前建议保持直连。",
    ),
    BuiltinSource("deribit", "Deribit", "期权数据", "www.deribit.com"),
    BuiltinSource("defillama", "DeFiLlama", "ETH 资金观察", "stablecoins.llama.fi"),
    BuiltinSource("farside", "Farside", "ETH 资金观察", "farside.co.uk"),
    BuiltinSource("deepseek", "DeepSeek API", "新闻分析", "api.deepseek.com"),
)


def default_uses_proxy(source_key: str) -> bool:
    return source_key in DEFAULT_PROXY_SOURCE_KEYS


def get_source_network_policy(source_key: str) -> SourceNetworkPolicy:
    policy, _ = SourceNetworkPolicy.objects.get_or_create(
        source_key=source_key,
        defaults={"use_proxy": default_uses_proxy(source_key)},
    )
    return policy


def source_proxy_url(source_key: str) -> str:
    if not get_source_network_policy(source_key).use_proxy:
        return ""
    proxy_url = settings.SOURCE_PROXY_URL
    if not proxy_url:
        raise ValueError(
            f"数据源 {source_key} 已配置使用代理，但 SOURCE_PROXY_URL 未配置。"
        )
    return proxy_url


def safe_proxy_label() -> str:
    proxy_url = settings.SOURCE_PROXY_URL
    if not proxy_url:
        return "未配置"
    parts = urlsplit(proxy_url)
    host = parts.hostname or "已配置"
    port = f":{parts.port}" if parts.port else ""
    scheme = f"{parts.scheme}://" if parts.scheme else ""
    return f"{scheme}{host}{port}"
