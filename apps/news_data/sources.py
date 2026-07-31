from __future__ import annotations

from dataclasses import dataclass


ETHEREUM_FOUNDATION_CODE = "ethereum_foundation"
BINANCE_ANNOUNCEMENTS_CODE = "binance_announcements"


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    code: str
    name: str
    collection_method: str
    observation_scope: str
    base_url: str
    feed_url: str
    parser_version: str


SOURCE_DEFINITIONS = {
    ETHEREUM_FOUNDATION_CODE: SourceDefinition(
        code=ETHEREUM_FOUNDATION_CODE,
        name="Ethereum Foundation Blog",
        collection_method="rss",
        observation_scope="eth_direct",
        base_url="https://blog.ethereum.org",
        feed_url="https://blog.ethereum.org/en/feed.xml",
        parser_version="ef-rss-v1",
    ),
    BINANCE_ANNOUNCEMENTS_CODE: SourceDefinition(
        code=BINANCE_ANNOUNCEMENTS_CODE,
        name="Binance 官方公告",
        collection_method="web",
        observation_scope="crypto_systemic",
        base_url="https://www.binance.com",
        feed_url=(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
        ),
        parser_version="binance-cms-v1",
    ),
}

BINANCE_PAGE_SIZE = 20
BINANCE_SAFETY_PAGE_LIMIT = 25
BINANCE_LIST_PARAMS = {"type": 1, "pageSize": BINANCE_PAGE_SIZE}
BINANCE_ARTICLE_PATH = "/en/support/announcement/detail/{code}"
TRACKING_QUERY_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "source",
}
COLLECTION_OVERLAP_DAYS = 3
