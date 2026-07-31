from django.db import migrations
from django.utils import timezone


def seed_official_sources(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    activated_at = timezone.now()
    definitions = (
        {
            "code": "ethereum_foundation",
            "name": "Ethereum Foundation Blog",
            "source_type": "official",
            "collection_method": "rss",
            "observation_scope": "eth_direct",
            "base_url": "https://blog.ethereum.org",
            "feed_url": "https://blog.ethereum.org/en/feed.xml",
            "parser_version": "ef-rss-v1",
        },
        {
            "code": "binance_announcements",
            "name": "Binance 官方公告",
            "source_type": "official",
            "collection_method": "web",
            "observation_scope": "crypto_systemic",
            "base_url": "https://www.binance.com",
            "feed_url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
            "parser_version": "binance-cms-v1",
        },
    )
    for definition in definitions:
        NewsSource.objects.get_or_create(
            code=definition["code"],
            defaults={**definition, "enabled": True, "activated_at": activated_at},
        )


class Migration(migrations.Migration):
    dependencies = [("news_data", "0001_initial")]
    operations = [migrations.RunPython(seed_official_sources, migrations.RunPython.noop)]
