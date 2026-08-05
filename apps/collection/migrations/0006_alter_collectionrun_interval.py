from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0005_collectionrun_news_feed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectionrun",
            name="interval",
            field=models.CharField(
                blank=True,
                choices=[
                    ("1d", "1d"),
                    ("1h", "1h"),
                    ("5m", "5m"),
                    ("actual", "实际结算"),
                ],
                max_length=10,
            ),
        ),
    ]
