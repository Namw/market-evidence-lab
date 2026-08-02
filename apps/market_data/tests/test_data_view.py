from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.market_data.models import FundingRate, Kline, OpenInterest


START = datetime(2026, 5, 1, tzinfo=UTC)


def create_kline(interval, open_time, price="3000"):
    duration = timedelta(days=1) if interval == Kline.Interval.ONE_DAY else timedelta(hours=1)
    price = Decimal(price)
    return Kline.objects.create(
        exchange=Kline.Exchange.BINANCE,
        market_type=Kline.MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        interval=interval,
        open_time=open_time,
        close_time=open_time + duration - timedelta(milliseconds=1),
        open=price,
        high=price + Decimal("20"),
        low=price - Decimal("10"),
        close=price + Decimal("5"),
        volume=Decimal("842600"),
        quote_volume=Decimal("2527800000"),
        trade_count=100,
        taker_buy_base_volume=Decimal("400000"),
        taker_buy_quote_volume=Decimal("1200000000"),
    )


def create_hourly_range(start, day_count):
    for hour in range(day_count * 24):
        timestamp = start + timedelta(hours=hour)
        create_kline(Kline.Interval.ONE_HOUR, timestamp, str(3000 + hour))
        OpenInterest.objects.create(
            exchange=Kline.Exchange.BINANCE,
            market_type=Kline.MarketType.USD_M_FUTURES,
            symbol="ETHUSDT",
            period="1h",
            timestamp=timestamp,
            sum_open_interest=Decimal("1200000") + hour,
            sum_open_interest_value=Decimal("3600000000") + hour,
        )
        if hour % 8 == 0:
            FundingRate.objects.create(
                exchange=Kline.Exchange.BINANCE,
                market_type=Kline.MarketType.USD_M_FUTURES,
                symbol="ETHUSDT",
                funding_time=timestamp,
                funding_rate=Decimal("0.0001") + Decimal(hour) / Decimal("10000000"),
                mark_price=Decimal("3000") + hour,
                rate_type="settled",
            )


class MarketDataViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for day in range(65):
            create_kline(
                Kline.Interval.ONE_DAY,
                START + timedelta(days=day),
                str(2900 + day),
            )

    def setUp(self):
        self.url = reverse("market_data:index")

    def test_defaults_to_latest_day_and_limits_daily_chart_to_sixty_rows(self):
        latest = START + timedelta(days=64)
        create_hourly_range(latest - timedelta(days=1), 2)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_date"], latest.date())
        self.assertEqual(len(response.context["daily_chart_data"]), 60)
        self.assertEqual(
            response.context["daily_chart_data"][0]["open_time"][:10],
            (START + timedelta(days=5)).date().isoformat(),
        )
        self.assertEqual(len(response.context["hourly_chart_data"]), 48)
        self.assertEqual(len(response.context["oi_chart_data"]), 48)
        self.assertEqual(len(response.context["funding_chart_data"]), 6)
        self.assertEqual(response.context["range_start"], latest - timedelta(days=1))
        self.assertEqual(response.context["range_end"], latest + timedelta(days=1))

    def test_historical_selection_uses_previous_selected_and_following_days(self):
        selected = START + timedelta(days=30)
        create_hourly_range(selected - timedelta(days=1), 3)

        response = self.client.get(self.url, {"date": selected.date().isoformat()})

        self.assertEqual(response.context["selected_date"], selected.date())
        self.assertEqual(response.context["selected_detail"]["date"], selected.date().isoformat())
        self.assertEqual(len(response.context["hourly_chart_data"]), 72)
        self.assertEqual(response.context["range_start"], selected - timedelta(days=1))
        self.assertEqual(response.context["range_end"], selected + timedelta(days=2))

    def test_invalid_or_unavailable_date_falls_back_to_latest_daily_candle(self):
        response = self.client.get(self.url, {"date": "not-a-date"})
        latest = START + timedelta(days=64)

        self.assertEqual(response.context["selected_date"], latest.date())

        response = self.client.get(self.url, {"date": START.date().isoformat()})
        self.assertEqual(response.context["selected_date"], latest.date())

    def test_page_contains_linked_chart_controls_and_json_payloads(self):
        response = self.client.get(self.url)

        self.assertContains(response, '<body class="data-view-body">')
        self.assertContains(response, "数据查看")
        self.assertContains(response, "点击任意日 K 联动下方图表")
        self.assertContains(response, "OI / Funding")
        self.assertContains(response, 'id="market-data-daily"')
        self.assertContains(response, 'data-chart="hourly"')
        self.assertContains(response, 'data-chart="derivatives"')


class EmptyMarketDataViewTests(TestCase):
    def test_empty_database_renders_guidance_instead_of_chart_controls(self):
        response = self.client.get(reverse("market_data:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "暂无可查看的日 K 数据")
        self.assertNotContains(response, 'data-chart="daily"')
