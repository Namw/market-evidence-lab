import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inspection", "0005_newsinspectionrun_source_and_more"),
        ("news_data", "0003_news_feeds_and_regulators"),
    ]

    operations = [
        migrations.AddField(
            model_name="newsinspectionrun",
            name="feed",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="inspection_runs", to="news_data.newsfeed"),
        ),
    ]
