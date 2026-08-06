from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.market_data.deribit_analytics import build_deribit_options_context
from apps.market_data.models import (
    DeribitOptionInstrument,
    DeribitOptionMarketSnapshot,
    DeribitVolatilityIndexCandle,
)


NOW = datetime(2026, 8, 5, 4, tzinfo=UTC)


def create_instrument(*, instrument_id, expiry, strike, option_type):
    suffix = "C" if option_type == DeribitOptionInstrument.OptionType.CALL else "P"
    return DeribitOptionInstrument.objects.create(
        instrument_id=instrument_id,
        instrument_name=f"ETH-{expiry:%d%b%y}-{strike}-{suffix}".upper(),
        base_currency="ETH",
        quote_currency="USD",
        settlement_currency="ETH",
        option_type=option_type,
        strike=Decimal(str(strike)),
        expiration_time=expiry,
        creation_time=NOW - timedelta(days=100),
        contract_size=Decimal("1"),
        is_active=True,
        state="open",
    )


def create_snapshot(instrument, observed_at, *, iv, oi):
    return DeribitOptionMarketSnapshot.objects.create(
        instrument=instrument,
        observed_at=observed_at,
        source_timestamp=observed_at,
        underlying_price=Decimal("2000"),
        mark_price=Decimal("0.1"),
        mark_iv=Decimal(str(iv)),
        open_interest=Decimal(str(oi)),
        volume_24h=Decimal("10"),
        volume_usd_24h=Decimal("20000"),
    )


class DeribitOptionsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        instrument_id = 1
        for expiry_index, expiry in enumerate(
            (NOW + timedelta(days=30), NOW + timedelta(days=90))
        ):
            for strike_index, strike in enumerate((1900, 2100)):
                for option_type, iv_offset in (
                    (DeribitOptionInstrument.OptionType.CALL, 0),
                    (DeribitOptionInstrument.OptionType.PUT, 2),
                ):
                    instrument = create_instrument(
                        instrument_id=instrument_id,
                        expiry=expiry,
                        strike=strike,
                        option_type=option_type,
                    )
                    instrument_id += 1
                    current_iv = 50 + expiry_index * 10 + strike_index * 10 + iv_offset
                    create_snapshot(
                        instrument,
                        NOW - timedelta(hours=24),
                        iv=current_iv - 1,
                        oi=50,
                    )
                    create_snapshot(
                        instrument,
                        NOW,
                        iv=current_iv,
                        oi=100 + strike_index * 20,
                    )

        for open_time, close in (
            (NOW - timedelta(hours=24), "48"),
            (NOW, "50"),
        ):
            DeribitVolatilityIndexCandle.objects.create(
                currency="ETH",
                resolution="1h",
                open_time=open_time,
                close_time=open_time + timedelta(hours=1),
                open=Decimal(close),
                high=Decimal(close) + Decimal("1"),
                low=Decimal(close) - Decimal("1"),
                close=Decimal(close),
            )

    def test_page_uses_real_snapshot_data_and_atm_interpolation(self):
        response = self.client.get(reverse("market_data:deribit_options"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_data"])
        self.assertEqual(response.context["total_oi"]["value"], "880")
        self.assertTrue(response.context["total_oi"]["available"])
        self.assertEqual(response.context["term_structure"]["items"][0]["value"], "56.00%")
        self.assertContains(response, "ATM IV 期限结构")
        self.assertContains(response, "按行权价的 Call / Put OI")
        self.assertContains(response, "Mark IV（年化隐含波动率）")
        self.assertContains(response, "行权价 ÷ 标的价格（价内程度）")
        self.assertContains(response, "data-hover-chart", count=2)
        self.assertContains(response, 'data-x-label="到期日（X）"')
        self.assertContains(response, 'data-y-label="Mark IV（Y）"')
        self.assertContains(response, 'data-x-value="95.0%"')
        self.assertContains(response, 'data-y-value="56.00%"')
        self.assertContains(response, "24h +480")

    def test_expiry_query_selects_requested_real_expiry(self):
        requested = (NOW + timedelta(days=30)).date().isoformat()

        response = self.client.get(
            reverse("market_data:deribit_options"),
            {"expiry": requested},
        )

        self.assertEqual(response.context["selected_expiry"]["key"], requested)
        self.assertEqual(len(response.context["strike_rows"]), 2)
        self.assertContains(response, f'value="{requested}" selected')

    def test_oi_split_and_skew_are_derived_from_snapshot(self):
        context = build_deribit_options_context()

        first_expiry = context["expiry_groups"][0]
        self.assertEqual(first_expiry["call_oi"], Decimal("220"))
        self.assertEqual(first_expiry["put_oi"], Decimal("220"))
        self.assertEqual(len(context["skew"]["series"]), 2)
        first_point = context["skew"]["series"][0]["coordinates"][0]
        self.assertEqual(first_point["x_value"], "95.0%")
        self.assertEqual(first_point["y_value"], "51.00%")

    def test_navigation_marks_deribit_entry_active(self):
        response = self.client.get(reverse("market_data:deribit_options"))

        self.assertContains(
            response,
            '<a class="nav-subitem is-active" href="/market-data/deribit-options/" aria-current="page">Deribit 期权数据</a>',
            html=True,
        )


class EmptyDeribitOptionsViewTests(TestCase):
    def test_empty_database_shows_collection_guidance(self):
        response = self.client.get(reverse("market_data:deribit_options"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_data"])
        self.assertContains(response, "暂无 Deribit 期权快照")
        self.assertContains(response, reverse("scheduling:index"))
