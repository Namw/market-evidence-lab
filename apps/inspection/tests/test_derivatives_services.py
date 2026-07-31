from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.inspection.models import DerivativesInspectionRun
from apps.inspection.services import (
    _inspect_funding_rows,
    _inspect_open_interest_rows,
    inspect_funding_rates,
    inspect_open_interest,
)
from apps.market_data.models import FundingRate, OpenInterest


START = datetime(2026, 7, 1, tzinfo=UTC)


def oi_row(timestamp, quantity=Decimal("1000"), value=Decimal("3000000")):
    return OpenInterest(
        exchange="binance",
        market_type="usd_m_futures",
        symbol="ETHUSDT",
        period="1h",
        timestamp=timestamp,
        sum_open_interest=quantity,
        sum_open_interest_value=value,
    )


def funding_row(funding_time, rate=Decimal("0.0001"), mark=Decimal("3000")):
    return FundingRate(
        exchange="binance",
        market_type="usd_m_futures",
        symbol="ETHUSDT",
        funding_time=funding_time,
        funding_rate=rate,
        mark_price=mark,
    )


class DerivativesInspectionPureRuleTests(SimpleTestCase):
    def test_oi_duplicate_and_non_increasing_sequence_are_detected(self):
        rows = [
            oi_row(START),
            oi_row(START),
            oi_row(START - timedelta(hours=1)),
        ]

        result = _inspect_open_interest_rows(rows, START, START + timedelta(hours=1))

        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["sequence_issue_count"], 1)
        self.assertTrue(result["details"]["duplicate_timestamps"])
        self.assertTrue(result["details"]["sequence_issues"])

    def test_funding_duplicate_settlement_is_detected_with_real_timestamp_offsets(self):
        rows = [
            funding_row(START + timedelta(milliseconds=5)),
            funding_row(START + timedelta(milliseconds=20)),
        ]

        result = _inspect_funding_rows(rows, START, START + timedelta(hours=8))

        self.assertEqual(result["expected_count"], 1)
        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["misaligned_count"], 0)


class DerivativesInspectionServiceTests(TestCase):
    def test_complete_oi_range_passes(self):
        OpenInterest.objects.bulk_create(
            [oi_row(START + timedelta(hours=hour)) for hour in range(25)]
        )

        run = inspect_open_interest("ETHUSDT", START, START + timedelta(hours=25))

        self.assertEqual(run.status, DerivativesInspectionRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, DerivativesInspectionRun.QualityStatus.PASSED)
        self.assertEqual(run.expected_count, 25)
        self.assertEqual(run.actual_count, 25)

    def test_oi_gap_is_saved_as_quality_issue(self):
        OpenInterest.objects.bulk_create(
            [
                oi_row(START + timedelta(hours=hour))
                for hour in range(4)
                if hour != 2
            ]
        )

        run = inspect_open_interest("ETHUSDT", START, START + timedelta(hours=4))

        self.assertEqual(run.status, DerivativesInspectionRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, DerivativesInspectionRun.QualityStatus.ISSUES)
        self.assertEqual(run.missing_count, 1)
        self.assertEqual(run.details["missing_ranges"][0]["count"], 1)

    def test_empty_oi_range_is_explicit_issue(self):
        run = inspect_open_interest("ETHUSDT", START, START + timedelta(hours=2))

        self.assertEqual(run.empty_count, 1)
        self.assertTrue(run.details["no_data"])
        self.assertEqual(run.missing_count, 2)

    def test_complete_funding_range_passes(self):
        FundingRate.objects.bulk_create(
            [funding_row(START + timedelta(hours=hour)) for hour in (0, 8, 16)]
        )

        run = inspect_funding_rates("ETHUSDT", START, START + timedelta(days=1))

        self.assertEqual(run.quality_status, DerivativesInspectionRun.QualityStatus.PASSED)
        self.assertEqual(run.expected_count, 3)
        self.assertEqual(run.actual_count, 3)

    def test_funding_gap_is_saved_as_quality_issue(self):
        FundingRate.objects.bulk_create(
            [funding_row(START), funding_row(START + timedelta(hours=16))]
        )

        run = inspect_funding_rates("ETHUSDT", START, START + timedelta(days=1))

        self.assertEqual(run.status, DerivativesInspectionRun.Status.SUCCESS)
        self.assertEqual(run.quality_status, DerivativesInspectionRun.QualityStatus.ISSUES)
        self.assertEqual(run.missing_count, 1)
        self.assertIn("08:00:00", run.details["missing_settlements"][0])

    def test_invalid_derivatives_values_are_reported_without_mutation(self):
        oi = oi_row(START, quantity=Decimal("-1"))
        oi.save()
        funding = funding_row(START, mark=Decimal("0"))
        funding.save()

        oi_run = inspect_open_interest("ETHUSDT", START, START + timedelta(hours=1))
        funding_run = inspect_funding_rates("ETHUSDT", START, START + timedelta(hours=8))

        self.assertEqual(oi_run.invalid_numeric_count, 1)
        self.assertEqual(funding_run.invalid_numeric_count, 1)
        oi.refresh_from_db()
        funding.refresh_from_db()
        self.assertEqual(oi.sum_open_interest, Decimal("-1"))
        self.assertEqual(funding.mark_price, Decimal("0"))
