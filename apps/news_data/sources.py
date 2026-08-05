from __future__ import annotations

from dataclasses import dataclass


ETHEREUM_FOUNDATION_CODE = "ethereum_foundation"
BINANCE_ANNOUNCEMENTS_CODE = "binance_announcements"
SEC_CODE = "sec"
CFTC_CODE = "cftc"
TETHER_CODE = "tether_news"
SLOWMIST_CODE = "slowmist_hacked"
COINDESK_CODE = "coindesk"
CIRCLE_CODE = "circle_pressroom"
FEDERAL_RESERVE_CODE = "federal_reserve"
BLS_CODE = "bls"

SEC_PRESS_RELEASES_CODE = "sec_press_releases"
SEC_SPEECHES_STATEMENTS_CODE = "sec_speeches_statements"
SEC_LITIGATION_RELEASES_CODE = "sec_litigation_releases"
CFTC_GENERAL_PRESS_CODE = "cftc_general_press"
CFTC_ENFORCEMENT_PRESS_CODE = "cftc_enforcement_press"
CFTC_SPEECHES_TESTIMONY_CODE = "cftc_speeches_testimony"
TETHER_NEWS_CODE = "tether_news"
SLOWMIST_HACKED_CODE = "slowmist_hacked"
CIRCLE_PRESSROOM_CODE = "circle_pressroom"
FED_MONETARY_POLICY_CODE = "fed_monetary_policy"
BLS_EMPLOYMENT_SITUATION_CODE = "bls_employment_situation"
BLS_CPI_CODE = "bls_cpi"
BLS_PPI_CODE = "bls_ppi"


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    code: str
    name: str
    collection_method: str
    observation_scope: str
    base_url: str
    feed_url: str = ""
    parser_version: str = "multi-feed-v1"


@dataclass(frozen=True, slots=True)
class FeedDefinition:
    code: str
    source_code: str
    name: str
    collection_method: str
    feed_url: str
    parser_version: str
    bootstrap_visible_items: bool = False


SOURCE_DEFINITIONS = {
    ETHEREUM_FOUNDATION_CODE: SourceDefinition(
        code=ETHEREUM_FOUNDATION_CODE,
        name="Ethereum Foundation Blog",
        collection_method="rss",
        observation_scope="eth_direct",
        base_url="https://blog.ethereum.org",
        feed_url="https://blog.ethereum.org/en/feed.xml",
        parser_version="generic-rss-v2",
    ),
    BINANCE_ANNOUNCEMENTS_CODE: SourceDefinition(
        code=BINANCE_ANNOUNCEMENTS_CODE,
        name="Binance 官方公告",
        collection_method="web",
        observation_scope="crypto_systemic",
        base_url="https://www.binance.com",
        feed_url="https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
        parser_version="binance-cms-v1",
    ),
    SEC_CODE: SourceDefinition(
        code=SEC_CODE,
        name="SEC RSS",
        collection_method="rss",
        observation_scope="crypto_systemic",
        base_url="https://www.sec.gov",
    ),
    CFTC_CODE: SourceDefinition(
        code=CFTC_CODE,
        name="CFTC RSS",
        collection_method="rss",
        observation_scope="crypto_systemic",
        base_url="https://www.cftc.gov",
    ),
    TETHER_CODE: SourceDefinition(
        code=TETHER_CODE,
        name="Tether News",
        collection_method="web",
        observation_scope="crypto_systemic",
        base_url="https://tether.io",
        feed_url="https://tether.io/wp-json/wp/v2/posts",
        parser_version="tether-wp-v1",
    ),
    SLOWMIST_CODE: SourceDefinition(
        code=SLOWMIST_CODE,
        name="SlowMist Hacked",
        collection_method="web",
        observation_scope="crypto_systemic",
        base_url="https://hacked.slowmist.io",
        feed_url="https://hacked.slowmist.io/",
        parser_version="slowmist-hacked-html-v1",
    ),
    COINDESK_CODE: SourceDefinition(
        code=COINDESK_CODE,
        name="CoinDesk",
        collection_method="rss",
        observation_scope="crypto_systemic",
        base_url="https://www.coindesk.com",
        feed_url="https://www.coindesk.com/arc/outboundfeeds/rss",
        parser_version="generic-rss-v2",
    ),
    CIRCLE_CODE: SourceDefinition(
        code=CIRCLE_CODE,
        name="Circle Pressroom",
        collection_method="web",
        observation_scope="crypto_systemic",
        base_url="https://www.circle.com",
        feed_url="https://www.circle.com/pressroom",
        parser_version="circle-pressroom-html-v1",
    ),
    FEDERAL_RESERVE_CODE: SourceDefinition(
        code=FEDERAL_RESERVE_CODE,
        name="Federal Reserve Board",
        collection_method="rss",
        observation_scope="crypto_systemic",
        base_url="https://www.federalreserve.gov",
        parser_version="multi-feed-v1",
    ),
    BLS_CODE: SourceDefinition(
        code=BLS_CODE,
        name="U.S. Bureau of Labor Statistics",
        collection_method="rss",
        observation_scope="crypto_systemic",
        base_url="https://www.bls.gov",
        parser_version="multi-feed-v1",
    ),
}

FEED_DEFINITIONS = {
    ETHEREUM_FOUNDATION_CODE: FeedDefinition(
        code=ETHEREUM_FOUNDATION_CODE,
        source_code=ETHEREUM_FOUNDATION_CODE,
        name="Blog RSS",
        collection_method="rss",
        feed_url="https://blog.ethereum.org/en/feed.xml",
        parser_version="generic-rss-v2",
    ),
    BINANCE_ANNOUNCEMENTS_CODE: FeedDefinition(
        code=BINANCE_ANNOUNCEMENTS_CODE,
        source_code=BINANCE_ANNOUNCEMENTS_CODE,
        name="官方公告",
        collection_method="web",
        feed_url="https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
        parser_version="binance-cms-v1",
    ),
    SEC_PRESS_RELEASES_CODE: FeedDefinition(
        code=SEC_PRESS_RELEASES_CODE,
        source_code=SEC_CODE,
        name="新闻稿",
        collection_method="rss",
        feed_url="https://www.sec.gov/news/pressreleases.rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    SEC_SPEECHES_STATEMENTS_CODE: FeedDefinition(
        code=SEC_SPEECHES_STATEMENTS_CODE,
        source_code=SEC_CODE,
        name="演讲与声明",
        collection_method="rss",
        feed_url="https://www.sec.gov/news/speeches-statements.rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    SEC_LITIGATION_RELEASES_CODE: FeedDefinition(
        code=SEC_LITIGATION_RELEASES_CODE,
        source_code=SEC_CODE,
        name="诉讼公告",
        collection_method="rss",
        feed_url="https://www.sec.gov/enforcement-litigation/litigation-releases/rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    CFTC_GENERAL_PRESS_CODE: FeedDefinition(
        code=CFTC_GENERAL_PRESS_CODE,
        source_code=CFTC_CODE,
        name="综合新闻稿",
        collection_method="rss",
        feed_url="https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    CFTC_ENFORCEMENT_PRESS_CODE: FeedDefinition(
        code=CFTC_ENFORCEMENT_PRESS_CODE,
        source_code=CFTC_CODE,
        name="执法新闻稿",
        collection_method="rss",
        feed_url="https://www.cftc.gov/RSS/RSSENF/rssenf.xml",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    CFTC_SPEECHES_TESTIMONY_CODE: FeedDefinition(
        code=CFTC_SPEECHES_TESTIMONY_CODE,
        source_code=CFTC_CODE,
        name="演讲与证词",
        collection_method="rss",
        feed_url="https://www.cftc.gov/RSS/RSSST/rssst.xml",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    TETHER_NEWS_CODE: FeedDefinition(
        code=TETHER_NEWS_CODE,
        source_code=TETHER_CODE,
        name="官方新闻",
        collection_method="web",
        feed_url="https://tether.io/wp-json/wp/v2/posts",
        parser_version="tether-wp-v1",
        bootstrap_visible_items=True,
    ),
    SLOWMIST_HACKED_CODE: FeedDefinition(
        code=SLOWMIST_HACKED_CODE,
        source_code=SLOWMIST_CODE,
        name="安全事件",
        collection_method="web",
        feed_url="https://hacked.slowmist.io/",
        parser_version="slowmist-hacked-html-v1",
        bootstrap_visible_items=True,
    ),
    COINDESK_CODE: FeedDefinition(
        code=COINDESK_CODE,
        source_code=COINDESK_CODE,
        name="全部新闻 RSS",
        collection_method="rss",
        feed_url="https://www.coindesk.com/arc/outboundfeeds/rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    CIRCLE_PRESSROOM_CODE: FeedDefinition(
        code=CIRCLE_PRESSROOM_CODE,
        source_code=CIRCLE_CODE,
        name="Press Releases",
        collection_method="web",
        feed_url="https://www.circle.com/pressroom",
        parser_version="circle-pressroom-html-v1",
        bootstrap_visible_items=True,
    ),
    FED_MONETARY_POLICY_CODE: FeedDefinition(
        code=FED_MONETARY_POLICY_CODE,
        source_code=FEDERAL_RESERVE_CODE,
        name="Monetary Policy",
        collection_method="rss",
        feed_url="https://www.federalreserve.gov/feeds/press_monetary.xml",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    BLS_EMPLOYMENT_SITUATION_CODE: FeedDefinition(
        code=BLS_EMPLOYMENT_SITUATION_CODE,
        source_code=BLS_CODE,
        name="Employment Situation",
        collection_method="rss",
        feed_url="https://www.bls.gov/feed/empsit.rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    BLS_CPI_CODE: FeedDefinition(
        code=BLS_CPI_CODE,
        source_code=BLS_CODE,
        name="Consumer Price Index",
        collection_method="rss",
        feed_url="https://www.bls.gov/feed/cpi.rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
    BLS_PPI_CODE: FeedDefinition(
        code=BLS_PPI_CODE,
        source_code=BLS_CODE,
        name="Producer Price Index",
        collection_method="rss",
        feed_url="https://www.bls.gov/feed/ppi.rss",
        parser_version="generic-rss-v2",
        bootstrap_visible_items=True,
    ),
}

BLS_FEED_CODES = {
    BLS_EMPLOYMENT_SITUATION_CODE,
    BLS_CPI_CODE,
    BLS_PPI_CODE,
}

SEC_FEED_CODES = {
    SEC_PRESS_RELEASES_CODE,
    SEC_SPEECHES_STATEMENTS_CODE,
    SEC_LITIGATION_RELEASES_CODE,
}
SUMMARY_ONLY_SOURCE_CODES = {
    SEC_CODE,
    CFTC_CODE,
    TETHER_CODE,
    SLOWMIST_CODE,
    COINDESK_CODE,
    CIRCLE_CODE,
    FEDERAL_RESERVE_CODE,
    BLS_CODE,
}

BINANCE_PAGE_SIZE = 20
BINANCE_SAFETY_PAGE_LIMIT = 25
BINANCE_LIST_PARAMS = {"type": 1, "pageSize": BINANCE_PAGE_SIZE}
BINANCE_ARTICLE_PATH = "/en/support/announcement/detail/{code}"
TETHER_PAGE_SIZE = 20
TETHER_SAFETY_PAGE_LIMIT = 25
TETHER_LIST_PARAMS = {
    "categories": 3,
    "per_page": TETHER_PAGE_SIZE,
    "_embed": "author,wp:term",
}
SLOWMIST_SAFETY_PAGE_LIMIT = 25
SLOWMIST_LIST_PARAMS = {"c": ""}
CIRCLE_SAFETY_PAGE_LIMIT = 5
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
