import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0005_collectionrun_news_feed"),
        ("inspection", "0006_newsinspectionrun_feed"),
        ("news_data", "0003_news_feeds_and_regulators"),
        ("scheduling", "0002_newsworkflowschedule_newsworkflowrun"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsWorkflowFeedRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("collection_status", models.CharField(choices=[("pending", "待执行"), ("success", "成功"), ("partial", "部分成功"), ("failed", "失败"), ("not_run", "未执行")], default="pending", max_length=20)),
                ("quality_status", models.CharField(choices=[("pending", "待检查"), ("passed", "通过"), ("warning", "警告"), ("failed", "失败"), ("not_run", "未执行")], default="pending", max_length=20)),
                ("safe_error_summary", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("collection_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="news_workflow_feed_steps", to="collection.collectionrun")),
                ("feed", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="workflow_steps", to="news_data.newsfeed")),
                ("inspection_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="news_workflow_feed_steps", to="inspection.newsinspectionrun")),
                ("workflow_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feed_steps", to="scheduling.newsworkflowrun")),
            ],
            options={"ordering": ["feed__source__code", "feed__code"]},
        ),
        migrations.AddConstraint(
            model_name="newsworkflowfeedrun",
            constraint=models.UniqueConstraint(fields=("workflow_run", "feed"), name="news_workflow_feed_run_unique"),
        ),
    ]
