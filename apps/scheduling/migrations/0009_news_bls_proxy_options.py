from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0008_deribit_options_daily_schedule")]

    operations = [
        migrations.AddField(
            model_name="newsworkflowschedule",
            name="use_bls_proxy",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="newsworkflowrun",
            name="use_bls_proxy",
            field=models.BooleanField(default=False),
        ),
    ]
