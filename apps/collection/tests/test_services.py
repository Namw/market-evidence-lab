from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.collection.models import CollectionRun
from apps.collection.services import collect_klines
from apps.market_data.binance import BinanceClientError, KlinePayload
from apps.market_data.models import Kline


def payload(open_time=None, **overrides):
    open_time = open_time or datetime(2024, 1, 1, tzinfo=UTC)
    values = {
        "open_time": open_time,
        "close_time": open_time + timedelta(hours=1, milliseconds=-1),
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
    return KlinePayload(**values)


class FakeClient:
    def __init__(
        self,
        batches=(),
        *,
        error=None,
        request_count=1,
        received_count=0,
        skipped_count=0,
    ):
        self.batches = list(batches)
        self.error = error
        self.request_count = request_count
        self.received_count = received_count
        self.skipped_count = skipped_count

    def iter_batches(self, **_):
        for batch in self.batches:
            yield batch
        if self.error:
            raise self.error


class CollectionServiceTests(TestCase):
    range_start = datetime(2024, 1, 1, tzinfo=UTC)
    range_end = datetime(2024, 1, 2, tzinfo=UTC)

    def collect(self, client, interval="1h"):
        return collect_klines(
            "ETHUSDT",
            interval,
            self.range_start,
            self.range_end,
            client=client,
        )

    def test_successful_run_records_true_statistics(self):
        items = [payload(), payload(self.range_start + timedelta(hours=1))]

        run = self.collect(
            FakeClient([items], request_count=1, received_count=2)
        )

        self.assertEqual(run.status, CollectionRun.Status.SUCCESS)
        self.assertEqual(run.request_count, 1)
        self.assertEqual(run.received_count, 2)
        self.assertEqual(run.inserted_count, 2)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(run.skipped_count, 0)
        self.assertEqual(Kline.objects.count(), 2)

    def test_duplicate_collection_does_not_create_or_fake_update(self):
        item = payload()
        self.collect(FakeClient([[item]], received_count=1))

        second_run = self.collect(FakeClient([[item]], received_count=1))

        self.assertEqual(Kline.objects.count(), 1)
        self.assertEqual(second_run.inserted_count, 0)
        self.assertEqual(second_run.updated_count, 0)
        self.assertEqual(second_run.skipped_count, 1)

    def test_existing_record_is_updated_when_a_value_changes(self):
        self.collect(FakeClient([[payload()]], received_count=1))
        changed = payload(close=Decimal("1250.000000000000000001"))

        run = self.collect(FakeClient([[changed]], received_count=1))

        self.assertEqual(run.inserted_count, 0)
        self.assertEqual(run.updated_count, 1)
        self.assertEqual(run.skipped_count, 0)
        self.assertEqual(Kline.objects.get().close, changed.close)

    def test_first_request_failure_is_recorded_as_failed(self):
        run = self.collect(
            FakeClient(
                error=BinanceClientError("network unavailable"),
                request_count=3,
            )
        )

        self.assertEqual(run.status, CollectionRun.Status.FAILED)
        self.assertEqual(run.request_count, 3)
        self.assertEqual(run.inserted_count, 0)
        self.assertIn("network unavailable", run.error_message)
        self.assertIsNotNone(run.finished_at)

    def test_failure_after_saved_batch_is_recorded_as_partial(self):
        run = self.collect(
            FakeClient(
                [[payload()]],
                error=BinanceClientError("second page failed"),
                request_count=4,
                received_count=1,
            )
        )

        self.assertEqual(run.status, CollectionRun.Status.PARTIAL)
        self.assertEqual(run.inserted_count, 1)
        self.assertEqual(Kline.objects.count(), 1)
        self.assertIn("second page failed", run.error_message)

    def test_client_and_database_skips_are_combined(self):
        item = payload()
        self.collect(FakeClient([[item]], received_count=1))

        run = self.collect(
            FakeClient([[item]], received_count=2, skipped_count=1)
        )

        self.assertEqual(run.skipped_count, 2)

    def test_one_run_record_represents_one_interval(self):
        self.collect(FakeClient([[]], received_count=0), interval="1d")
        self.collect(FakeClient([[]], received_count=0), interval="1h")

        self.assertCountEqual(
            CollectionRun.objects.values_list("interval", flat=True),
            ["1d", "1h"],
        )
