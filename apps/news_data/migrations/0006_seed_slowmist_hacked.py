from django.db import migrations
from django.utils import timezone


def seed_slowmist_hacked(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsFeed = apps.get_model("news_data", "NewsFeed")
    now = timezone.now()
    source, _ = NewsSource.objects.get_or_create(
        code="slowmist_hacked",
        defaults={
            "name": "SlowMist Hacked",
            "enabled": True,
            "activated_at": now,
            "source_type": "official",
            "collection_method": "web",
            "observation_scope": "crypto_systemic",
            "authority_level": "medium",
            "base_url": "https://hacked.slowmist.io",
            "feed_url": "https://hacked.slowmist.io/",
            "parser_version": "slowmist-hacked-html-v1",
        },
    )
    NewsFeed.objects.get_or_create(
        code="slowmist_hacked",
        defaults={
            "source": source,
            "name": "安全事件",
            "enabled": True,
            "activated_at": source.activated_at,
            "feed_url": "https://hacked.slowmist.io/",
            "parser_version": "slowmist-hacked-html-v1",
            "bootstrap_visible_items": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("news_data", "0005_seed_tether_news")]

    operations = [
        migrations.RunPython(seed_slowmist_hacked, migrations.RunPython.noop),
    ]
