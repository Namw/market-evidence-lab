from django.db import migrations
from django.utils import timezone


def seed_circle_pressroom(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsFeed = apps.get_model("news_data", "NewsFeed")
    now = timezone.now()
    source, _ = NewsSource.objects.get_or_create(
        code="circle_pressroom",
        defaults={
            "name": "Circle Pressroom",
            "enabled": True,
            "activated_at": now,
            "source_type": "official",
            "collection_method": "web",
            "observation_scope": "crypto_systemic",
            "authority_level": "medium",
            "base_url": "https://www.circle.com",
            "feed_url": "https://www.circle.com/pressroom",
            "parser_version": "circle-pressroom-html-v1",
        },
    )
    NewsFeed.objects.get_or_create(
        code="circle_pressroom",
        defaults={
            "source": source,
            "name": "Press Releases",
            "enabled": True,
            "activated_at": source.activated_at,
            "feed_url": "https://www.circle.com/pressroom",
            "parser_version": "circle-pressroom-html-v1",
            "bootstrap_visible_items": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("news_data", "0007_seed_coindesk")]

    operations = [
        migrations.RunPython(seed_circle_pressroom, migrations.RunPython.noop),
    ]
