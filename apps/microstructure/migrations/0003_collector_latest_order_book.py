from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("microstructure", "0002_microstructurecollectorrun"),
    ]

    operations = [
        migrations.AddField(
            model_name="microstructurecollectorrun",
            name="latest_asks",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="microstructurecollectorrun",
            name="latest_bids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="microstructurecollectorrun",
            name="latest_update_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
