from django.db import migrations, models


def set_source_authority(apps, schema_editor):
    NewsSource = apps.get_model("news_data", "NewsSource")
    NewsSource.objects.filter(
        code__in=["ethereum_foundation", "sec", "cftc"]
    ).update(authority_level="highest")
    NewsSource.objects.filter(code="binance_announcements").update(
        authority_level="medium"
    )
    NewsSource.objects.filter(code="sec").update(name="SEC RSS")
    NewsSource.objects.filter(code="cftc").update(name="CFTC RSS")


class Migration(migrations.Migration):
    dependencies = [("news_data", "0003_news_feeds_and_regulators")]

    operations = [
        migrations.AddField(
            model_name="newssource",
            name="authority_level",
            field=models.CharField(
                choices=[
                    ("highest", "最高"),
                    ("medium", "中等"),
                    ("general", "一般"),
                ],
                default="general",
                max_length=20,
            ),
        ),
        migrations.RunPython(set_source_authority, migrations.RunPython.noop),
    ]
