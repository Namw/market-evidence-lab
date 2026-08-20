from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("microstructure", "0006_marketminute_future_5m_return"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketminute",
            name="imbalance_top5_close",
            field=models.DecimalField(
                blank=True,
                decimal_places=18,
                max_digits=40,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="marketminute",
            name="imbalance_top5_mean",
            field=models.DecimalField(
                blank=True,
                decimal_places=18,
                max_digits=40,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="marketminute",
            name="imbalance_top5_sample_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="marketminute",
            name="imbalance_top5_sum",
            field=models.DecimalField(decimal_places=18, default=0, max_digits=50),
        ),
    ]
