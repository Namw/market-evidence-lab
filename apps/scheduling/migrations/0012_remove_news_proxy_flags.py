from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0011_funddataschedule_funddataworkflowrun")]

    operations = [
        migrations.RemoveField(
            model_name="newsworkflowschedule",
            name="use_source_proxy",
        ),
        migrations.RemoveField(
            model_name="newsworkflowrun",
            name="use_source_proxy",
        ),
    ]
