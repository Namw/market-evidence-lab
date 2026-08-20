from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("microstructure", "0005_collector_oi_process_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketminute",
            name="future_5m_return",
            field=models.DecimalField(
                blank=True,
                decimal_places=18,
                help_text="严格连续五分钟后的收盘价相对当前收盘价收益。",
                max_digits=40,
                null=True,
            ),
        ),
    ]
