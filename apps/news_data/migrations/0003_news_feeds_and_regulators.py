import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def seed_feeds(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsFeed = apps.get_model("news_data", "NewsFeed")
    NewsRawRecord = apps.get_model("news_data", "NewsRawRecord")
    NewsRecordFeed = apps.get_model("news_data", "NewsRecordFeed")
    now = timezone.now()
    sources = {
        "ethereum_foundation": {
            "name": "Ethereum Foundation Blog",
            "method": "rss",
            "scope": "eth_direct",
            "base_url": "https://blog.ethereum.org",
        },
        "binance_announcements": {
            "name": "Binance 官方公告",
            "method": "web",
            "scope": "crypto_systemic",
            "base_url": "https://www.binance.com",
        },
        "sec": {
            "name": "U.S. Securities and Exchange Commission (SEC)",
            "method": "rss",
            "scope": "crypto_systemic",
            "base_url": "https://www.sec.gov",
        },
        "cftc": {
            "name": "U.S. Commodity Futures Trading Commission (CFTC)",
            "method": "rss",
            "scope": "crypto_systemic",
            "base_url": "https://www.cftc.gov",
        },
    }
    source_objects = {}
    for code, definition in sources.items():
        source, _ = NewsSource.objects.get_or_create(
            code=code,
            defaults={
                "name": definition["name"],
                "enabled": True,
                "activated_at": now,
                "source_type": "official",
                "collection_method": definition["method"],
                "observation_scope": definition["scope"],
                "base_url": definition["base_url"],
                "feed_url": "",
                "parser_version": "multi-feed-v1",
            },
        )
        source_objects[code] = source

    feeds = (
        ("ethereum_foundation", "ethereum_foundation", "Blog RSS", "https://blog.ethereum.org/en/feed.xml", "generic-rss-v2", False),
        ("binance_announcements", "binance_announcements", "官方公告", "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query", "binance-cms-v1", False),
        ("sec_press_releases", "sec", "新闻稿", "https://www.sec.gov/news/pressreleases.rss", "generic-rss-v2", True),
        ("sec_speeches_statements", "sec", "演讲与声明", "https://www.sec.gov/news/speeches-statements.rss", "generic-rss-v2", True),
        ("sec_litigation_releases", "sec", "诉讼公告", "https://www.sec.gov/enforcement-litigation/litigation-releases/rss", "generic-rss-v2", True),
        ("cftc_general_press", "cftc", "综合新闻稿", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "generic-rss-v2", True),
        ("cftc_enforcement_press", "cftc", "执法新闻稿", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml", "generic-rss-v2", True),
        ("cftc_speeches_testimony", "cftc", "演讲与证词", "https://www.cftc.gov/RSS/RSSST/rssst.xml", "generic-rss-v2", True),
    )
    feed_objects = {}
    for code, source_code, name, url, parser_version, bootstrap in feeds:
        source = source_objects[source_code]
        legacy = code == source_code
        feed, _ = NewsFeed.objects.get_or_create(
            code=code,
            defaults={
                "source": source,
                "name": name,
                "enabled": True,
                "activated_at": source.activated_at,
                "feed_url": url,
                "parser_version": parser_version,
                "bootstrap_visible_items": bootstrap,
                "last_run_at": source.last_run_at if legacy else None,
                "trusted_coverage_end": source.trusted_coverage_end if legacy else None,
                "last_inspection_status": source.last_inspection_status if legacy else "never_run",
                "health_status": source.health_status if legacy else "never_run",
            },
        )
        feed_objects[code] = feed

    for record in NewsRawRecord.objects.select_related(
        "source", "first_collection_run", "last_collection_run"
    ):
        feed = feed_objects.get(record.source.code)
        if feed is None:
            continue
        NewsRecordFeed.objects.get_or_create(
            news_record=record,
            feed=feed,
            defaults={
                "first_seen_at": record.first_seen_at,
                "last_seen_at": record.last_seen_at,
                "first_collection_run": record.first_collection_run,
                "last_collection_run": record.last_collection_run,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0004_collectionrun_news_source_and_more"),
        ("news_data", "0002_seed_official_sources"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsFeed",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=160)),
                ("enabled", models.BooleanField(default=True)),
                ("activated_at", models.DateTimeField()),
                ("feed_url", models.URLField(max_length=500)),
                ("parser_version", models.CharField(max_length=80)),
                ("bootstrap_visible_items", models.BooleanField(default=False)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("trusted_coverage_end", models.DateTimeField(blank=True, null=True)),
                ("last_inspection_status", models.CharField(choices=[("never_run", "从未运行"), ("passed", "通过"), ("warning", "警告"), ("failed", "失败")], default="never_run", max_length=20)),
                ("health_status", models.CharField(choices=[("never_run", "从未运行"), ("healthy", "健康"), ("degraded", "降级"), ("broken", "故障")], default="never_run", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="feeds", to="news_data.newssource")),
            ],
            options={"ordering": ["source__code", "code"]},
        ),
        migrations.AddConstraint(
            model_name="newsfeed",
            constraint=models.UniqueConstraint(fields=("source", "name"), name="news_feed_source_name_unique"),
        ),
        migrations.AddField(
            model_name="newsrawrecord",
            name="source_author",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="NewsRecordFeed",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_seen_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
                ("feed", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="record_memberships", to="news_data.newsfeed")),
                ("first_collection_run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="first_seen_news_feed_memberships", to="collection.collectionrun")),
                ("last_collection_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="last_seen_news_feed_memberships", to="collection.collectionrun")),
                ("news_record", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feed_memberships", to="news_data.newsrawrecord")),
            ],
        ),
        migrations.AddConstraint(
            model_name="newsrecordfeed",
            constraint=models.UniqueConstraint(fields=("news_record", "feed"), name="news_record_feed_unique"),
        ),
        migrations.AddField(
            model_name="newsrawrecord",
            name="feeds",
            field=models.ManyToManyField(related_name="raw_records", through="news_data.NewsRecordFeed", to="news_data.newsfeed"),
        ),
        migrations.AddField(
            model_name="newscollectiondiagnostic",
            name="details",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="newscollectiondiagnostic",
            name="feed",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="diagnostics", to="news_data.newsfeed"),
        ),
        migrations.RunPython(seed_feeds, migrations.RunPython.noop),
    ]
