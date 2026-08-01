import re
from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_navigation = False
        self.navigation_depth = 0
        self.labels = []
        self.links = []
        self.groups = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav" and "navigation" in attributes.get("class", "").split():
            self.in_navigation = True
            self.navigation_depth = 1
            return
        if not self.in_navigation:
            return
        self.navigation_depth += 1
        if tag == "a":
            self.links.append(attributes["href"])
        elif tag == "details":
            self.groups.append(attributes["data-nav-group"])

    def handle_endtag(self, tag):
        if not self.in_navigation:
            return
        self.navigation_depth -= 1
        if self.navigation_depth == 0:
            self.in_navigation = False

    def handle_data(self, data):
        if self.in_navigation and data.strip():
            self.labels.append(data.strip())


def navigation_html(response):
    html = response.content.decode()
    return re.search(r'<nav class="navigation">(.*?)</nav>', html, re.DOTALL).group(1)


def parsed_navigation(response):
    parser = NavigationParser()
    parser.feed(response.content.decode())
    return parser


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


class SidebarNavigationTests(TestCase):
    expected_links = [
        ("core:home", None),
        ("market_monitoring:index", "market-monitoring"),
        ("research_cases:list", "research-cases"),
        ("inspection:index", "kline"),
        ("collection:index", "kline"),
        ("collection:derivatives", "derivatives"),
        ("news_analysis:index", "news"),
        ("news_data:index", "news"),
        ("scheduling:index", "system"),
    ]

    def test_sidebar_uses_real_entries_in_fixed_order_without_duplicates(self):
        response = self.client.get(reverse("core:home"))
        navigation = parsed_navigation(response)

        self.assertEqual(
            navigation.labels,
            [
                "总览",
                "今日价值巡检",
                "今日巡检结果",
                "研究案例",
                "案例列表",
                "K 线观察",
                "数据检查",
                "数据采集",
                "OI / Funding 观察",
                "数据采集",
                "新闻观察",
                "每日分析结果",
                "数据采集",
                "系统管理",
                "自动调度",
            ],
        )
        self.assertEqual(
            navigation.links,
            [reverse(url_name) for url_name, _ in self.expected_links],
        )
        self.assertEqual(
            navigation.groups,
            [
                "market-monitoring",
                "research-cases",
                "kline",
                "derivatives",
                "news",
                "system",
            ],
        )

    def test_sidebar_omits_unbuilt_entries_and_status_labels(self):
        response = self.client.get(reverse("core:home"))
        navigation = navigation_html(response)

        for unavailable_text in (
            "AI 报告",
            "待建设",
            "V1",
            "已建设",
            "独立链路",
            "案例容器",
        ):
            with self.subTest(unavailable_text=unavailable_text):
                self.assertNotIn(unavailable_text, navigation)

        kline_group = re.search(
            r'<details[^>]*data-nav-group="kline".*?</details>',
            navigation,
            re.DOTALL,
        ).group(0)
        derivatives_group = re.search(
            r'<details[^>]*data-nav-group="derivatives".*?</details>',
            navigation,
            re.DOTALL,
        ).group(0)
        self.assertNotIn("每日分析结果", kline_group)
        self.assertNotIn("每日分析结果", derivatives_group)

    def test_home_is_direct_active_entry_and_no_group_defaults_open(self):
        response = self.client.get(reverse("core:home"))
        navigation = navigation_html(response)

        self.assertIn(
            f'<a class="nav-item is-active" href="{reverse("core:home")}" aria-current="page">',
            navigation,
        )
        self.assertNotRegex(navigation, r"<details[^>]*\sopen(?:\s|>)")

    def test_each_page_opens_only_its_group_and_marks_one_child_active(self):
        for url_name, expected_group in self.expected_links[1:]:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                navigation = navigation_html(response)
                groups = re.findall(
                    r'<details class="([^"]*)" data-nav-group="([^"]+)"([^>]*)>',
                    navigation,
                )
                active_groups = [
                    (classes, group, attributes)
                    for classes, group, attributes in groups
                    if "is-active" in classes.split()
                ]

                self.assertEqual(len(active_groups), 1)
                classes, group, attributes = active_groups[0]
                self.assertEqual(group, expected_group)
                self.assertIn("is-active", classes.split())
                self.assertIn(" open", attributes)
                self.assertEqual(
                    len(re.findall(r'<details[^>]*\sopen(?:\s|>)', navigation)),
                    1,
                )
                self.assertEqual(navigation.count('aria-current="page"'), 1)
                self.assertIn(f'href="{reverse(url_name)}" aria-current="page"', navigation)

    def test_query_string_detail_states_keep_namespace_group_mapping(self):
        detail_urls = (
            (f'{reverse("market_monitoring:index")}?run=999999', "market-monitoring"),
            (f'{reverse("inspection:index")}?run=999999', "kline"),
            (f'{reverse("collection:index")}?run=999999', "kline"),
            (f'{reverse("collection:derivatives")}?run=999999', "derivatives"),
            (f'{reverse("news_analysis:index")}?page=2', "news"),
            (f'{reverse("news_data:index")}?run=999999', "news"),
            (f'{reverse("scheduling:index")}?run=999999&news_run=999999', "system"),
        )

        for url, expected_group in detail_urls:
            with self.subTest(url=url):
                navigation = navigation_html(self.client.get(url))
                self.assertRegex(
                    navigation,
                    rf'<details class="nav-group is-active" data-nav-group="{expected_group}" open>',
                )
