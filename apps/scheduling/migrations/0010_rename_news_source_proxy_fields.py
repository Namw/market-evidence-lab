from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0009_news_bls_proxy_options")]

    operations = [
        migrations.RenameField(
            model_name="newsworkflowschedule",
            old_name="use_bls_proxy",
            new_name="use_source_proxy",
        ),
        migrations.RenameField(
            model_name="newsworkflowrun",
            old_name="use_bls_proxy",
            new_name="use_source_proxy",
        ),
    ]
