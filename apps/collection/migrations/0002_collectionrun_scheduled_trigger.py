from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectionrun",
            name="trigger",
            field=models.CharField(
                choices=[("scheduled", "定时"), ("manual", "手工")],
                default="manual",
                max_length=20,
            ),
        ),
    ]
