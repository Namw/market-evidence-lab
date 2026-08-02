import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0004_collectionrun_news_source_and_more"),
        ("news_data", "0003_news_feeds_and_regulators"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionrun",
            name="news_feed",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="collection_runs", to="news_data.newsfeed"),
        ),
    ]
