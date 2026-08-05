from django.db import migrations
from django.utils import timezone


SOURCES = (
    (
        "federal_reserve",
        "Federal Reserve Board",
        "https://www.federalreserve.gov",
    ),
    (
        "bls",
        "U.S. Bureau of Labor Statistics",
        "https://www.bls.gov",
    ),
)

FEEDS = (
    (
        "fed_monetary_policy",
        "federal_reserve",
        "Monetary Policy",
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
    ),
    (
        "bls_employment_situation",
        "bls",
        "Employment Situation",
        "https://www.bls.gov/feed/empsit.rss",
    ),
    (
        "bls_cpi",
        "bls",
        "Consumer Price Index",
        "https://www.bls.gov/feed/cpi.rss",
    ),
    (
        "bls_ppi",
        "bls",
        "Producer Price Index",
        "https://www.bls.gov/feed/ppi.rss",
    ),
)


def seed_fed_bls_rss(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsFeed = apps.get_model("news_data", "NewsFeed")
    now = timezone.now()
    source_objects = {}
    for code, name, base_url in SOURCES:
        source, _ = NewsSource.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "enabled": True,
                "activated_at": now,
                "source_type": "official",
                "collection_method": "rss",
                "observation_scope": "crypto_systemic",
                "authority_level": "highest",
                "base_url": base_url,
                "feed_url": "",
                "parser_version": "multi-feed-v1",
            },
        )
        source_objects[code] = source

    for code, source_code, name, feed_url in FEEDS:
        source = source_objects[source_code]
        NewsFeed.objects.get_or_create(
            code=code,
            defaults={
                "source": source,
                "name": name,
                "enabled": True,
                "activated_at": source.activated_at,
                "feed_url": feed_url,
                "parser_version": "generic-rss-v2",
                "bootstrap_visible_items": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("news_data", "0008_seed_circle_pressroom")]

    operations = [
        migrations.RunPython(seed_fed_bls_rss, migrations.RunPython.noop),
    ]
