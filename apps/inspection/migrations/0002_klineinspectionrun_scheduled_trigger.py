from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inspection", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="klineinspectionrun",
            name="trigger",
            field=models.CharField(
                choices=[("scheduled", "定时"), ("manual", "手工")],
                default="manual",
                max_length=20,
            ),
        ),
    ]
