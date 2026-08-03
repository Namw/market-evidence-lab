from django.db import migrations, models
from django.utils import timezone


def seed_coindesk(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsFeed = apps.get_model("news_data", "NewsFeed")
    now = timezone.now()
    source, _ = NewsSource.objects.get_or_create(
        code="coindesk",
        defaults={
            "name": "CoinDesk",
            "enabled": True,
            "activated_at": now,
            "source_type": "media",
            "collection_method": "rss",
            "observation_scope": "crypto_systemic",
            "authority_level": "general",
            "base_url": "https://www.coindesk.com",
            "feed_url": "https://www.coindesk.com/arc/outboundfeeds/rss",
            "parser_version": "generic-rss-v2",
        },
    )
    NewsFeed.objects.get_or_create(
        code="coindesk",
        defaults={
            "source": source,
            "name": "全部新闻 RSS",
            "enabled": True,
            "activated_at": source.activated_at,
            "feed_url": "https://www.coindesk.com/arc/outboundfeeds/rss",
            "parser_version": "generic-rss-v2",
            "bootstrap_visible_items": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("news_data", "0006_seed_slowmist_hacked")]

    operations = [
        migrations.AlterField(
            model_name="newssource",
            name="source_type",
            field=models.CharField(
                choices=[("official", "官方"), ("media", "媒体")],
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_coindesk, migrations.RunPython.noop),
    ]
