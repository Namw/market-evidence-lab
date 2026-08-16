import re
from html.parser import HTMLParser

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_navigation = False
        self.depth = 0
        self.labels = []
        self.links = []
        self.groups = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav" and "navigation" in attributes.get("class", "").split():
            self.in_navigation = True
            self.depth = 1
            return
        if not self.in_navigation:
            return
        self.depth += 1
        if tag == "a":
            self.links.append(attributes["href"])
        elif tag == "details":
            self.groups.append(attributes["data-nav-group"])

    def handle_endtag(self, tag):
        if not self.in_navigation:
            return
        self.depth -= 1
        if self.depth == 0:
            self.in_navigation = False

    def handle_data(self, data):
        if self.in_navigation and data.strip():
            self.labels.append(data.strip())


class ProductSurfaceTests(TestCase):
    expected_links = [
        "market_data:index",
        "market_data:deribit_options",
        "market_funds:index",
        "market_funds:addresses",
        "microstructure:index",
        "news_data:index",
        "news_analysis:index",
        "news_analysis:objective_fact_list",
        "news_analysis:event_overview",
        "scheduling:index",
        "scheduling:sources",
        "scheduling:runs",
    ]

    def test_human_facing_timezone_is_beijing_while_aware_datetimes_remain_enabled(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Shanghai")
        self.assertTrue(settings.USE_TZ)

    def test_sidebar_contains_only_requested_product_entries(self):
        response = self.client.get(reverse("market_data:index"))
        parser = NavigationParser()
        parser.feed(response.content.decode())

        self.assertEqual(
            parser.groups,
            ["market-data", "market-funds", "microstructure", "news", "collection"],
        )
        self.assertEqual(
            parser.labels,
            [
                "行情数据观察",
                "数据查看",
                "Deribit 期权数据",
                "ETH 资金观察",
                "资金与流动性",
                "地址变化",
                "微观结构",
                "盘口采集",
                "新闻观察",
                "数据采集",
                "每日分析结果",
                "客观事实提取",
                "新闻事件库",
                "采集",
                "自动调度",
                "来源与网络",
                "调度情况",
            ],
        )
        self.assertEqual(parser.links, [reverse(name) for name in self.expected_links])

    def test_root_renders_home_page_and_links_to_three_product_areas(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/home.html")
        self.assertContains(response, "观察市场事实")
        self.assertContains(response, "行情数据观察")
        self.assertContains(response, "新闻观察")
        self.assertContains(response, "采集")
        self.assertContains(response, f'href="{reverse("market_data:index")}"')
        self.assertContains(response, f'href="{reverse("news_analysis:event_overview")}"')
        self.assertContains(response, f'href="{reverse("scheduling:index")}"')
        self.assertContains(response, f'href="{reverse("core:home")}" aria-label="返回首页"')

    def test_requested_entry_pages_are_available(self):
        for name in self.expected_links:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_removed_public_pages_return_not_found(self):
        for path in (
            "/overview/",
            "/collection/",
            "/collection/derivatives/",
            "/inspection/",
            "/market-inspection/",
            "/research-cases/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_each_requested_area_marks_only_its_navigation_group_active(self):
        expected_groups = {
            "scheduling:index": "collection",
            "scheduling:runs": "collection",
            "news_data:index": "news",
            "news_analysis:index": "news",
            "news_analysis:objective_fact_list": "news",
            "news_analysis:event_overview": "news",
            "market_data:index": "market-data",
            "market_funds:index": "market-funds",
            "market_funds:addresses": "market-funds",
        }
        for name, expected_group in expected_groups.items():
            with self.subTest(name=name):
                html = self.client.get(reverse(name)).content.decode()
                navigation = re.search(
                    r'<nav class="navigation">(.*?)</nav>', html, re.DOTALL
                ).group(1)
                active_groups = re.findall(
                    r'<details class="nav-group is-active" data-nav-group="([^"]+)" open>',
                    navigation,
                )
                self.assertEqual(active_groups, [expected_group])
                self.assertEqual(navigation.count('aria-current="page"'), 1)
