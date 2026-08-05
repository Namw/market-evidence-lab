from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.collection.models import CollectionRun
from apps.collection.pipeline import collect_and_inspect
from apps.inspection.models import DerivativesInspectionRun, KlineInspectionRun
from apps.market_data.binance import KlinePayload
from apps.market_data.derivatives import FundingRatePayload, OpenInterestPayload


START = datetime(2026, 7, 1, tzinfo=UTC)


class FakeClient:
    def __init__(self, batches):
        self.batches = batches
        self.request_count = len(batches)
        self.received_count = sum(map(len, batches))
        self.skipped_count = 0

    def iter_batches(self, **_kwargs):
        yield from self.batches


def kline_payload(open_time):
    return KlinePayload(
        open_time=open_time,
        close_time=open_time + timedelta(hours=1, milliseconds=-1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=10,
        taker_buy_base_volume=Decimal("5"),
        taker_buy_quote_volume=Decimal("500"),
    )


class CollectionPipelineTests(TestCase):
    def test_kline_collection_is_followed_by_linked_quality_inspection(self):
        payloads = [kline_payload(START + timedelta(hours=hour)) for hour in range(24)]

        result = collect_and_inspect(
            data_type=CollectionRun.DataType.KLINE,
            symbol="ETHUSDT",
            interval="1h",
            range_start=START,
            range_end=START + timedelta(days=1),
            client=FakeClient([payloads]),
        )

        self.assertEqual(result.collection_run.status, CollectionRun.Status.SUCCESS)
        self.assertEqual(result.inspection_run.quality_status, KlineInspectionRun.QualityStatus.PASSED)
        self.assertEqual(
            result.inspection_run.source_collection_run,
            result.collection_run,
        )

    def test_oi_pipeline_includes_end_boundary_and_passes_complete_data(self):
        payloads = [
            OpenInterestPayload(
                START + timedelta(hours=hour),
                Decimal("1000"),
                Decimal("3000000"),
            )
            for hour in range(25)
        ]

        result = collect_and_inspect(
            data_type=CollectionRun.DataType.OPEN_INTEREST,
            symbol="ETHUSDT",
            range_start=START,
            range_end=START + timedelta(days=1),
            client=FakeClient([payloads]),
        )

        self.assertEqual(result.collection_run.range_end, START + timedelta(hours=25))
        self.assertEqual(
            result.inspection_run.quality_status,
            DerivativesInspectionRun.QualityStatus.PASSED,
        )
        self.assertEqual(result.inspection_run.expected_count, 25)

    def test_five_minute_oi_pipeline_uses_five_minute_boundary_and_quality_step(self):
        payloads = [
            OpenInterestPayload(
                START + timedelta(minutes=5 * index),
                Decimal("1000"),
                Decimal("3000000"),
            )
            for index in range(13)
        ]

        result = collect_and_inspect(
            data_type=CollectionRun.DataType.OPEN_INTEREST,
            symbol="ETHUSDT",
            interval="5m",
            range_start=START,
            range_end=START + timedelta(hours=1),
            client=FakeClient([payloads]),
        )

        self.assertEqual(result.collection_run.interval, "5m")
        self.assertEqual(result.collection_run.range_end, START + timedelta(minutes=65))
        self.assertEqual(result.inspection_run.expected_count, 13)
        self.assertEqual(
            result.inspection_run.quality_status,
            DerivativesInspectionRun.QualityStatus.PASSED,
        )

    def test_funding_pipeline_uses_actual_eight_hour_settlements(self):
        payloads = [
            FundingRatePayload(
                START + timedelta(hours=hour, milliseconds=hour),
                Decimal("0.0001"),
                Decimal("3000"),
                "",
            )
            for hour in (0, 8, 16)
        ]

        result = collect_and_inspect(
            data_type=CollectionRun.DataType.FUNDING,
            symbol="ETHUSDT",
            range_start=START,
            range_end=START + timedelta(days=1),
            client=FakeClient([payloads]),
        )

        self.assertEqual(result.collection_run.range_end, START + timedelta(days=1))
        self.assertEqual(
            result.inspection_run.quality_status,
            DerivativesInspectionRun.QualityStatus.PASSED,
        )
        self.assertEqual(result.inspection_run.expected_count, 3)

    def test_collection_success_is_preserved_when_quality_finds_gap(self):
        result = collect_and_inspect(
            data_type=CollectionRun.DataType.FUNDING,
            symbol="ETHUSDT",
            range_start=START,
            range_end=START + timedelta(days=1),
            client=FakeClient(
                [[FundingRatePayload(START, Decimal("0.0001"), Decimal("3000"), "")]]
            ),
        )

        self.assertEqual(result.collection_run.status, CollectionRun.Status.SUCCESS)
        self.assertEqual(
            result.inspection_run.quality_status,
            DerivativesInspectionRun.QualityStatus.ISSUES,
        )
