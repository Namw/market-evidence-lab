from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.news_data.models import NewsSource


class NewsCollectionViewTests(TestCase):
    def test_page_is_available_and_shows_both_official_sources(self):
        response = self.client.get(reverse("news_data:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ethereum Foundation Blog")
        self.assertContains(response, "Binance 官方公告")
        self.assertContains(response, "零可解析内容")
        self.assertContains(response, "新闻数据采集")

    @patch("apps.news_data.views.collect_and_inspect")
    def test_manual_entry_uses_unified_pipeline(self, collect):
        collect.return_value = SimpleNamespace(
            collection_run=SimpleNamespace(),
            inspection_run=SimpleNamespace(
                quality_status="passed", inserted_count=0, updated_count=0
            ),
        )
        source = NewsSource.objects.get(code="ethereum_foundation")

        response = self.client.post(reverse("news_data:run", args=[source.code]))

        self.assertRedirects(response, reverse("news_data:index"))
        self.assertEqual(collect.call_args.kwargs["data_type"], "news")
        self.assertEqual(collect.call_args.kwargs["source_code"], source.code)

    def test_manual_entry_keeps_csrf_protection(self):
        source = NewsSource.objects.get(code="ethereum_foundation")
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(reverse("news_data:run", args=[source.code]))

        self.assertEqual(response.status_code, 403)

    def test_navigation_marks_news_collection_active(self):
        response = self.client.get(reverse("news_data:index"))

        self.assertContains(
            response,
            '<details class="nav-group is-active" data-nav-group="news" open>',
        )
        self.assertContains(response, 'href="/collection/news/" aria-current="page"')
