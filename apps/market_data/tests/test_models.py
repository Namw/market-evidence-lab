from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.market_data.models import Kline


def make_kline(**overrides):
    values = {
        "exchange": Kline.Exchange.BINANCE,
        "market_type": Kline.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": Kline.Interval.ONE_HOUR,
        "open_time": datetime(2024, 1, 1, tzinfo=UTC),
        "close_time": datetime(2024, 1, 1, tzinfo=UTC)
        + timedelta(hours=1, milliseconds=-1),
        "open": Decimal("1234.123456789012345678"),
        "high": Decimal("1240.000000000000000001"),
        "low": Decimal("1230.000000000000000001"),
        "close": Decimal("1238.123456789012345678"),
        "volume": Decimal("100.123456789012345678"),
        "quote_volume": Decimal("123456.123456789012345678"),
        "trade_count": 42,
        "taker_buy_base_volume": Decimal("55.123456789012345678"),
        "taker_buy_quote_volume": Decimal("67890.123456789012345678"),
    }
    values.update(overrides)
    return Kline.objects.create(**values)


class KlineModelTests(TestCase):
    def test_unique_constraint_rejects_duplicate_market_open_time(self):
        make_kline()

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_kline()

    def test_decimal_fields_preserve_numeric_precision(self):
        kline = make_kline()

        kline.refresh_from_db()

        self.assertEqual(kline.open, Decimal("1234.123456789012345678"))
        self.assertEqual(kline.quote_volume, Decimal("123456.123456789012345678"))
        self.assertEqual(
            kline.taker_buy_quote_volume,
            Decimal("67890.123456789012345678"),
        )
