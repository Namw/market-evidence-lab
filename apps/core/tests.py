from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_home_page_returns_http_200(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)

    def test_home_page_uses_correct_template(self):
        response = self.client.get(reverse("core:home"))

        self.assertTemplateUsed(response, "core/home.html")

    def test_home_page_contains_brand_name(self):
        response = self.client.get(reverse("core:home"))

        self.assertContains(response, "Market Evidence Lab")

    def test_home_page_contains_complete_system_flow(self):
        response = self.client.get(reverse("core:home"))

        for stage in ("采集", "分析", "巡检", "研究案例", "AI报告", "人工反馈"):
            with self.subTest(stage=stage):
                self.assertContains(response, stage)
