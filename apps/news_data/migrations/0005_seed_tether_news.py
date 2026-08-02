from django.db import migrations
from django.utils import timezone


def seed_tether_news(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsFeed = apps.get_model("news_data", "NewsFeed")
    now = timezone.now()
    source, _ = NewsSource.objects.get_or_create(
        code="tether_news",
        defaults={
            "name": "Tether News",
            "enabled": True,
            "activated_at": now,
            "source_type": "official",
            "collection_method": "web",
            "observation_scope": "crypto_systemic",
            "authority_level": "medium",
            "base_url": "https://tether.io",
            "feed_url": "https://tether.io/wp-json/wp/v2/posts",
            "parser_version": "tether-wp-v1",
        },
    )
    NewsFeed.objects.get_or_create(
        code="tether_news",
        defaults={
            "source": source,
            "name": "官方新闻",
            "enabled": True,
            "activated_at": source.activated_at,
            "feed_url": "https://tether.io/wp-json/wp/v2/posts",
            "parser_version": "tether-wp-v1",
            "bootstrap_visible_items": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("news_data", "0004_news_source_authority")]

    operations = [
        migrations.RunPython(seed_tether_news, migrations.RunPython.noop),
    ]
