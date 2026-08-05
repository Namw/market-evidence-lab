from django.db import migrations, models


DEFAULT_PROXY_SOURCE_KEYS = (
    "binance_announcements",
    "bls",
    "coindesk",
    "deribit",
    "sec",
)


def seed_default_proxy_policies(apps, schema_editor):
    policy = apps.get_model("collection", "SourceNetworkPolicy")
    policy.objects.bulk_create(
        [policy(source_key=source_key, use_proxy=True) for source_key in DEFAULT_PROXY_SOURCE_KEYS],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [("collection", "0008_alter_collectionrun_data_type")]

    operations = [
        migrations.CreateModel(
            name="SourceNetworkPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=80, unique=True)),
                ("use_proxy", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["source_key"]},
        ),
        migrations.RunPython(seed_default_proxy_policies, migrations.RunPython.noop),
    ]
