from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("news_analysis", "0003_eth_directional_classification")]

    operations = [
        migrations.AlterField(
            model_name="newsanalysisresult",
            name="classification_stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("title_rule", "程序判断标题"),
                    ("title_ai", "AI 判断标题"),
                    ("summary_ai", "AI 判断 RSS 摘要"),
                    ("content_ai", "AI 判断正文"),
                ],
                max_length=20,
            ),
        )
    ]
