from django.apps import AppConfig


class NewsDataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.news_data"
    verbose_name = "新闻原始数据"
