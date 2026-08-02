from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def reset_legacy_classifications(apps, schema_editor):
    NewsAnalysisResult = apps.get_model("news_analysis", "NewsAnalysisResult")
    NewsRawRecord = apps.get_model("news_data", "NewsRawRecord")
    irrelevant_ids = list(
        NewsAnalysisResult.objects.filter(observation_result="noise").values_list(
            "news_record_id", flat=True
        )
    )
    expired_unclear_ids = list(
        NewsAnalysisResult.objects.filter(
            observation_result="insufficient",
            analyzed_at__lt=timezone.now() - timedelta(days=3),
        ).values_list("news_record_id", flat=True)
    )
    NewsAnalysisResult.objects.all().delete()
    NewsRawRecord.objects.filter(
        id__in=set(irrelevant_ids + expired_unclear_ids)
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("news_analysis", "0002_alter_newsanalysisrun_status_and_more")]

    operations = [
        migrations.AddField(
            model_name="newsanalysisresult",
            name="conclusion",
            field=models.CharField(
                blank=True,
                choices=[
                    ("bullish", "利好"),
                    ("bearish", "利空"),
                    ("unclear", "模糊不清"),
                    ("irrelevant", "无关"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="newsanalysisresult",
            name="classification_stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("title_rule", "程序判断标题"),
                    ("title_ai", "AI 判断标题"),
                    ("content_ai", "AI 判断正文"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="newsanalysisresult",
            name="content_summary",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(reset_legacy_classifications, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="newsanalysisresult",
            name="news_an_res_ver_obs_idx",
        ),
        migrations.RemoveField(model_name="newsanalysisresult", name="confidence"),
        migrations.RemoveField(model_name="newsanalysisresult", name="event_type"),
        migrations.RemoveField(model_name="newsanalysisresult", name="impact_scope"),
        migrations.RemoveField(model_name="newsanalysisresult", name="importance"),
        migrations.RemoveField(
            model_name="newsanalysisresult", name="observation_result"
        ),
        migrations.AddIndex(
            model_name="newsanalysisresult",
            index=models.Index(
                fields=["analysis_version", "conclusion"],
                name="news_an_res_ver_concl_idx",
            ),
        ),
    ]
