from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("market_data", "0002_fundingrate_openinterest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="kline",
            name="interval",
            field=models.CharField(
                choices=[("1d", "1d"), ("1h", "1h"), ("5m", "5m")],
                max_length=5,
            ),
        ),
        migrations.AlterField(
            model_name="openinterest",
            name="period",
            field=models.CharField(
                choices=[("1h", "1h"), ("5m", "5m")],
                max_length=5,
            ),
        ),
    ]
