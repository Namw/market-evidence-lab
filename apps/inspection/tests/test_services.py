from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.inspection.models import KlineInspectionRun
from apps.inspection.services import DETAIL_LIMIT, inspect_klines
from apps.market_data.models import Kline


RANGE_START = datetime(2024, 1, 1, tzinfo=UTC)


def make_kline(open_time, interval="1h", **overrides):
    step = timedelta(days=1) if interval == "1d" else timedelta(hours=1)
    values = {
        "exchange": Kline.Exchange.BINANCE,
        "market_type": Kline.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": interval,
        "open_time": open_time,
        "close_time": open_time + step - timedelta(milliseconds=1),
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("90"),
        "close": Decimal("105"),
        "volume": Decimal("10"),
        "quote_volume": Decimal("1000"),
        "trade_count": 10,
        "taker_buy_base_volume": Decimal("5"),
        "taker_buy_quote_volume": Decimal("500"),
    }
    values.update(overrides)
    return Kline(**values)


def create_complete_range(start, count, interval="1h"):
    step = timedelta(days=1) if interval == "1d" else timedelta(hours=1)
    Kline.objects.bulk_create(
        [make_kline(start + index * step, interval) for index in range(count)]
    )


class InspectionServiceTests(TestCase):
    def inspect(self, interval, start, end):
        return inspect_klines("ETHUSDT", interval, start, end)

    def test_complete_1d_data_passes(self):
        create_complete_range(RANGE_START, 2, "1d")

        run = self.inspect("1d", RANGE_START, RANGE_START + timedelta(days=2))

        self.assertEqual(run.status, KlineInspectionRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, KlineInspectionRun.QualityStatus.PASSED)
        self.assertEqual(run.expected_count, 2)
        self.assertEqual(run.actual_count, 2)

    def test_complete_1h_data_passes(self):
        create_complete_range(RANGE_START, 24, "1h")

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(days=1))

        self.assertEqual(run.quality_status, KlineInspectionRun.QualityStatus.PASSED)
        self.assertEqual(run.expected_count, 24)
        self.assertEqual(run.actual_count, 24)

    def test_empty_database_reports_every_expected_kline_missing(self):
        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(days=1))

        self.assertEqual(run.missing_count, 24)
        self.assertEqual(run.actual_count, 0)
        self.assertEqual(run.details["missing_ranges"][0]["count"], 24)

    def test_single_missing_kline_is_found(self):
        rows = [
            make_kline(RANGE_START + timedelta(hours=index))
            for index in range(24)
            if index != 12
        ]
        Kline.objects.bulk_create(rows)

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(days=1))

        self.assertEqual(run.missing_count, 1)
        self.assertEqual(len(run.details["missing_ranges"]), 1)
        self.assertIn("12:00:00", run.details["missing_ranges"][0]["start"])

    def test_continuous_missing_klines_are_compressed(self):
        rows = [
            make_kline(RANGE_START + timedelta(hours=index))
            for index in range(24)
            if index not in {5, 6, 7}
        ]
        Kline.objects.bulk_create(rows)

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(days=1))

        self.assertEqual(run.missing_count, 3)
        self.assertEqual(len(run.details["missing_ranges"]), 1)
        self.assertEqual(run.details["missing_ranges"][0]["count"], 3)
        self.assertIn("05:00:00", run.details["missing_ranges"][0]["start"])
        self.assertIn("08:00:00", run.details["missing_ranges"][0]["end"])

    def test_multiple_missing_segments_are_recorded_separately(self):
        rows = [
            make_kline(RANGE_START + timedelta(hours=index))
            for index in range(8)
            if index not in {2, 5}
        ]
        Kline.objects.bulk_create(rows)

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=8))

        self.assertEqual(run.missing_count, 2)
        self.assertEqual(len(run.details["missing_ranges"]), 2)

    def test_invalid_ohlc_is_detected(self):
        make_kline(RANGE_START, high=Decimal("95")).save()

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.invalid_ohlc_count, 1)
        self.assertIn("high_below_open", run.details["invalid_rows"][0]["rules"])

    def test_negative_volume_is_detected(self):
        Kline.objects.bulk_create([make_kline(RANGE_START, volume=Decimal("-1"))])

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.invalid_numeric_count, 1)
        self.assertIn("volume_negative", run.details["invalid_rows"][0]["rules"])

    def test_misaligned_open_time_is_detected(self):
        misaligned = RANGE_START + timedelta(minutes=30)
        Kline.objects.bulk_create([make_kline(misaligned)])

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.misaligned_count, 1)
        self.assertEqual(run.missing_count, 1)
        self.assertIn("00:30:00", run.details["misaligned_open_times"][0])

    def test_wrong_close_time_is_detected(self):
        Kline.objects.bulk_create(
            [make_kline(RANGE_START, close_time=RANGE_START + timedelta(hours=1))]
        )

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.invalid_close_time_count, 1)
        self.assertIn("close_time_mismatch", run.details["invalid_rows"][0]["rules"])

    def test_multiple_issue_categories_are_counted_together(self):
        Kline.objects.bulk_create(
            [
                make_kline(
                    RANGE_START,
                    high=Decimal("80"),
                    volume=Decimal("-1"),
                    close_time=RANGE_START + timedelta(hours=1),
                )
            ]
        )

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.invalid_ohlc_count, 1)
        self.assertEqual(run.invalid_numeric_count, 1)
        self.assertEqual(run.invalid_close_time_count, 1)
        self.assertEqual(run.quality_status, KlineInspectionRun.QualityStatus.ISSUES)

    def test_details_are_truncated_but_total_statistics_remain_exact(self):
        count = DETAIL_LIMIT + 5
        Kline.objects.bulk_create(
            [
                make_kline(
                    RANGE_START + timedelta(hours=index),
                    volume=Decimal("-1"),
                )
                for index in range(count)
            ],
            batch_size=100,
        )

        run = self.inspect(
            "1h",
            RANGE_START,
            RANGE_START + timedelta(hours=count),
        )

        self.assertEqual(run.invalid_numeric_count, count)
        self.assertEqual(len(run.details["invalid_rows"]), DETAIL_LIMIT)
        self.assertTrue(run.details["details_truncated"])

    def test_issues_are_a_successful_program_run(self):
        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.status, KlineInspectionRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, KlineInspectionRun.QualityStatus.ISSUES)

    def test_no_issues_sets_quality_passed(self):
        Kline.objects.bulk_create([make_kline(RANGE_START)])

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.quality_status, KlineInspectionRun.QualityStatus.PASSED)
        self.assertFalse(run.details["details_truncated"])

    @patch("apps.inspection.services._perform_inspection")
    def test_execution_exception_is_recorded_as_failed(self, perform):
        perform.side_effect = RuntimeError("inspection query unavailable")

        run = self.inspect("1h", RANGE_START, RANGE_START + timedelta(hours=1))

        self.assertEqual(run.status, KlineInspectionRun.Status.FAILED)
        self.assertEqual(run.quality_status, KlineInspectionRun.QualityStatus.PENDING)
        self.assertIn("inspection query unavailable", run.error_message)
        self.assertIsNotNone(run.finished_at)

    def test_service_rejects_unaligned_range(self):
        with self.assertRaisesRegex(ValueError, "align"):
            self.inspect(
                "1h",
                RANGE_START + timedelta(minutes=1),
                RANGE_START + timedelta(hours=1),
            )
