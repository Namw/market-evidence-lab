from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.collection.deribit import (
    collect_deribit_dvol,
    collect_deribit_option_instruments,
    collect_deribit_option_snapshot,
)
from apps.collection.models import CollectionRun
from apps.market_data.deribit import (
    DvolCandlePayload,
    OptionInstrumentPayload,
    OptionSummaryPayload,
)
from apps.market_data.models import (
    DeribitOptionInstrument,
    DeribitOptionMarketSnapshot,
    DeribitVolatilityIndexCandle,
)


START = datetime(2026, 8, 5, 1, 2, tzinfo=UTC)


def instrument_payload(name="ETH-28AUG26-4000-C", instrument_id=123):
    return OptionInstrumentPayload(
        instrument_id=instrument_id,
        instrument_name=name,
        base_currency="ETH",
        quote_currency="ETH",
        settlement_currency="ETH",
        option_type="call" if name.endswith("-C") else "put",
        strike=Decimal("4000"),
        expiration_time=datetime(2026, 8, 28, 8, tzinfo=UTC),
        creation_time=datetime(2026, 7, 1, tzinfo=UTC),
        contract_size=Decimal("1"),
        is_active=True,
        state="open",
    )


def summary_payload(mark_iv="68.1"):
    return OptionSummaryPayload(
        instrument_name="ETH-28AUG26-4000-C",
        source_timestamp=START,
        underlying_price=Decimal("3900"),
        mark_price=Decimal("0.05"),
        mark_iv=Decimal(mark_iv),
        bid_price=Decimal("0.049"),
        ask_price=Decimal("0.051"),
        mid_price=Decimal("0.05"),
        last_price=None,
        open_interest=Decimal("1250.5"),
        volume_24h=Decimal("42"),
        volume_usd_24h=Decimal("163800"),
        interest_rate=Decimal("0.01"),
    )


class FakeDeribitClient:
    def __init__(self, *, instruments=None, summaries=None, dvol_batches=None):
        self.instruments = instruments or []
        self.summaries = summaries or []
        self.dvol_batches = dvol_batches or []
        self.request_count = 0
        self.received_count = 0
        self.skipped_count = 0

    def fetch_option_instruments(self, *, currency):
        self.request_count += 1
        self.received_count += len(self.instruments)
        return self.instruments

    def fetch_option_summaries(self, *, currency):
        self.request_count += 1
        self.received_count += len(self.summaries)
        return self.summaries

    def iter_dvol_batches(self, **kwargs):
        self.request_count += 1
        self.received_count += sum(map(len, self.dvol_batches))
        yield from self.dvol_batches

    def close(self):
        pass


class DeribitCollectionTests(TestCase):
    def test_instrument_sync_is_idempotent_and_deactivates_expired_rows(self):
        expired = instrument_payload("ETH-07AUG26-3500-P", 99)
        DeribitOptionInstrument.objects.create(
            instrument_id=expired.instrument_id,
            instrument_name=expired.instrument_name,
            base_currency=expired.base_currency,
            quote_currency=expired.quote_currency,
            settlement_currency=expired.settlement_currency,
            option_type=expired.option_type,
            strike=expired.strike,
            expiration_time=expired.expiration_time,
            creation_time=expired.creation_time,
            contract_size=expired.contract_size,
            is_active=True,
            state="open",
        )
        client = FakeDeribitClient(instruments=[instrument_payload()])

        run = collect_deribit_option_instruments(observed_at=START, client=client)

        self.assertEqual(run.status, CollectionRun.Status.SUCCESS)
        self.assertEqual(run.inserted_count, 1)
        self.assertEqual(run.updated_count, 1)
        self.assertFalse(
            DeribitOptionInstrument.objects.get(instrument_id=99).is_active
        )

        rerun = collect_deribit_option_instruments(observed_at=START, client=client)
        self.assertEqual(rerun.skipped_count, 1)

    def test_snapshot_uses_five_minute_bucket_and_updates_idempotently(self):
        collect_deribit_option_instruments(
            observed_at=START,
            client=FakeDeribitClient(instruments=[instrument_payload()]),
        )
        client = FakeDeribitClient(summaries=[summary_payload()])

        first = collect_deribit_option_snapshot(observed_at=START, client=client)
        second = collect_deribit_option_snapshot(
            observed_at=START + timedelta(minutes=2), client=client
        )

        self.assertEqual(first.inserted_count, 1)
        self.assertEqual(second.skipped_count, 1)
        self.assertEqual(DeribitOptionMarketSnapshot.objects.count(), 1)
        row = DeribitOptionMarketSnapshot.objects.get()
        self.assertEqual(row.observed_at, START.replace(minute=0))
        self.assertEqual(row.open_interest, Decimal("1250.5"))

        changed = collect_deribit_option_snapshot(
            observed_at=START,
            client=FakeDeribitClient(summaries=[summary_payload("70.25")]),
        )
        self.assertEqual(changed.updated_count, 1)
        row.refresh_from_db()
        self.assertEqual(row.mark_iv, Decimal("70.25"))

    def test_snapshot_fails_safely_when_metadata_is_missing(self):
        run = collect_deribit_option_snapshot(
            observed_at=START,
            client=FakeDeribitClient(summaries=[summary_payload()]),
        )

        self.assertEqual(run.status, CollectionRun.Status.FAILED)
        self.assertEqual(DeribitOptionMarketSnapshot.objects.count(), 0)
        self.assertNotIn("ETH-28AUG26", run.error_message)

    def test_dvol_collection_is_idempotent(self):
        candle = DvolCandlePayload(
            open_time=datetime(2026, 8, 5, tzinfo=UTC),
            close_time=datetime(2026, 8, 5, 1, tzinfo=UTC),
            open=Decimal("60"),
            high=Decimal("64"),
            low=Decimal("59"),
            close=Decimal("63"),
        )
        client = FakeDeribitClient(dvol_batches=[[candle]])

        first = collect_deribit_dvol(candle.open_time, candle.close_time, client=client)
        second = collect_deribit_dvol(candle.open_time, candle.close_time, client=client)

        self.assertEqual(first.inserted_count, 1)
        self.assertEqual(second.skipped_count, 1)
        self.assertEqual(DeribitVolatilityIndexCandle.objects.count(), 1)


class DeribitCollectionCommandTests(TestCase):
    def make_run(self, data_type):
        return CollectionRun.objects.create(
            data_type=data_type,
            exchange=CollectionRun.Exchange.DERIBIT,
            market_type=CollectionRun.MarketType.OPTIONS,
            symbol="ETH",
            interval=CollectionRun.Interval.FIVE_MINUTES,
            range_start=START,
            range_end=START + timedelta(minutes=5),
            status=CollectionRun.Status.SUCCESS,
            started_at=START,
            finished_at=START,
        )

    @patch(
        "apps.collection.management.commands.collect_deribit_options.collect_deribit_option_snapshot"
    )
    @patch(
        "apps.collection.management.commands.collect_deribit_options.collect_deribit_option_instruments"
    )
    def test_skip_dvol_collects_metadata_before_snapshot(self, instruments, snapshot):
        instruments.return_value = self.make_run(
            CollectionRun.DataType.DERIBIT_OPTION_INSTRUMENT
        )
        snapshot.return_value = self.make_run(
            CollectionRun.DataType.DERIBIT_OPTION_SNAPSHOT
        )
        stdout = StringIO()

        call_command("collect_deribit_options", "--skip-dvol", stdout=stdout)

        instruments.assert_called_once()
        snapshot.assert_called_once()
        self.assertIn("collection complete", stdout.getvalue())
