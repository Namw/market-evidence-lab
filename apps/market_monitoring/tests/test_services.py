from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.market_data.models import Kline
from apps.market_monitoring.models import MarketAnomalyFinding, MarketScanRun
from apps.market_monitoring.services import (
    PRICE_CHANGE_THRESHOLD_PCT,
    VOLUME_BASELINE_DAYS,
    VOLUME_RATIO_THRESHOLD,
    WICK_BODY_RATIO_THRESHOLD,
    WICK_RANGE_RATIO_THRESHOLD,
    rules_snapshot,
    scan_market_anomalies,
)


DAY = datetime(2024, 2, 1, tzinfo=UTC)


def make_kline(open_time, **overrides):
    values = {
        "exchange": Kline.Exchange.BINANCE,
        "market_type": Kline.MarketType.USD_M_FUTURES,
        "symbol": "ETHUSDT",
        "interval": Kline.Interval.ONE_DAY,
        "open_time": open_time,
        "close_time": open_time + timedelta(days=1) - timedelta(milliseconds=1),
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("98"),
        "close": Decimal("101"),
        "volume": Decimal("100"),
        "quote_volume": Decimal("10000"),
        "trade_count": 10,
        "taker_buy_base_volume": Decimal("50"),
        "taker_buy_quote_volume": Decimal("5000"),
    }
    values.update(overrides)
    return Kline.objects.create(**values)


def make_baseline(day=DAY, volume=Decimal("100")):
    for days_ago in range(VOLUME_BASELINE_DAYS, 0, -1):
        make_kline(day - timedelta(days=days_ago), volume=volume)


def scan(start=DAY, end=DAY + timedelta(days=1)):
    return scan_market_anomalies(start, end)


class RangeValidationTests(TestCase):
    def test_range_is_left_closed_and_right_open(self):
        make_kline(DAY)
        make_kline(DAY + timedelta(days=1))

        run = scan()

        self.assertEqual(run.expected_count, 1)
        self.assertEqual(run.actual_count, 1)

    def test_unclosed_utc_day_cannot_be_scanned(self):
        current_boundary = timezone.now().astimezone(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        with self.assertRaisesRegex(ValueError, "closed UTC days"):
            scan_market_anomalies(current_boundary, current_boundary + timedelta(days=1))

    def test_range_cannot_exceed_366_days(self):
        with self.assertRaisesRegex(ValueError, "366"):
            scan_market_anomalies(DAY, DAY + timedelta(days=367))


class PriceChangeRuleTests(TestCase):
    def test_exactly_five_percent_up_triggers_boundary(self):
        make_kline(DAY, close=Decimal("105"), high=Decimal("106"), low=Decimal("99"))

        run = scan()

        finding = run.findings.get()
        self.assertEqual(finding.price_change_pct, Decimal("5"))
        self.assertEqual(finding.signals[0]["type"], "abnormal_change_up")

    def test_exactly_minus_five_percent_triggers_boundary(self):
        make_kline(DAY, close=Decimal("95"), high=Decimal("101"), low=Decimal("94"))

        run = scan()

        finding = run.findings.get()
        self.assertEqual(finding.price_change_pct, Decimal("-5"))
        self.assertEqual(finding.signals[0]["type"], "abnormal_change_down")

    def test_price_change_below_threshold_does_not_trigger(self):
        make_kline(
            DAY,
            close=Decimal("104.999"),
            high=Decimal("106"),
            low=Decimal("99"),
        )

        run = scan()

        self.assertEqual(run.anomaly_day_count, 0)


class VolumeRuleTests(TestCase):
    def test_exactly_two_times_average_triggers(self):
        make_baseline()
        make_kline(DAY, volume=Decimal("200"))

        run = scan()

        finding = run.findings.get()
        self.assertEqual(finding.volume_average_20, Decimal("100"))
        self.assertEqual(finding.volume_ratio, Decimal("2"))
        self.assertIn("volume_spike", [signal["type"] for signal in finding.signals])

    def test_current_day_is_not_included_in_twenty_day_average(self):
        make_baseline(volume=Decimal("10"))
        make_kline(DAY, volume=Decimal("210"))

        finding = scan().findings.get()

        self.assertEqual(finding.volume_average_20, Decimal("10"))
        self.assertEqual(finding.volume_ratio, Decimal("21"))

    def test_all_twenty_consecutive_days_make_baseline_available(self):
        make_baseline()
        make_kline(DAY)

        run = scan()

        self.assertEqual(run.volume_baseline_unavailable_count, 0)
        self.assertEqual(run.evaluated_count, 1)

    def test_missing_baseline_day_does_not_use_older_kline_to_fill_gap(self):
        make_kline(DAY - timedelta(days=21), volume=Decimal("100"))
        for days_ago in range(20, 1, -1):
            make_kline(DAY - timedelta(days=days_ago), volume=Decimal("100"))
        make_kline(DAY, volume=Decimal("300"))

        run = scan()

        self.assertEqual(run.volume_baseline_unavailable_count, 1)
        self.assertEqual(run.anomaly_day_count, 0)

    def test_unavailable_baseline_does_not_block_price_and_wick_rules(self):
        make_kline(
            DAY,
            close=Decimal("105"),
            high=Decimal("120"),
            low=Decimal("99"),
        )

        run = scan()

        finding = run.findings.get()
        self.assertEqual(run.volume_baseline_unavailable_count, 1)
        self.assertEqual(
            {signal["type"] for signal in finding.signals},
            {"abnormal_change_up", "long_upper_wick"},
        )
        self.assertIsNone(finding.volume_average_20)


class WickRuleTests(TestCase):
    def test_long_upper_wick_requires_both_ratios(self):
        make_kline(DAY, open=Decimal("100"), close=Decimal("101"), high=Decimal("104"), low=Decimal("90"))
        first = scan()
        self.assertNotIn(
            "long_upper_wick",
            [signal["type"] for signal in first.findings.get().signals],
        )

        Kline.objects.filter(open_time=DAY).update(low=Decimal("99"))
        second = scan()
        self.assertIn(
            "long_upper_wick",
            [signal["type"] for signal in second.findings.get().signals],
        )

    def test_long_lower_wick_requires_both_ratios(self):
        make_kline(DAY, open=Decimal("100"), close=Decimal("99"), high=Decimal("110"), low=Decimal("96"))
        first = scan()
        self.assertNotIn(
            "long_lower_wick",
            [signal["type"] for signal in first.findings.get().signals],
        )

        Kline.objects.filter(open_time=DAY).update(high=Decimal("101"))
        second = scan()
        self.assertIn(
            "long_lower_wick",
            [signal["type"] for signal in second.findings.get().signals],
        )

    def test_doji_body_zero_does_not_divide_by_zero(self):
        make_kline(DAY, open=Decimal("100"), close=Decimal("100"), high=Decimal("110"), low=Decimal("90"))

        finding = scan().findings.get()

        self.assertIsNone(finding.upper_wick_body_ratio)
        self.assertIsNone(finding.lower_wick_body_ratio)
        self.assertEqual(
            {signal["type"] for signal in finding.signals},
            {"long_upper_wick", "long_lower_wick"},
        )


class ScanStatisticsTests(TestCase):
    def test_multiple_signals_on_one_day_create_one_finding(self):
        make_kline(DAY, close=Decimal("105"), high=Decimal("120"), low=Decimal("99"))

        run = scan()

        self.assertEqual(run.anomaly_day_count, 1)
        self.assertEqual(run.signal_count, 2)
        self.assertEqual(run.findings.count(), 1)

    def test_signal_and_anomaly_counts_use_different_units(self):
        make_kline(DAY, close=Decimal("105"), high=Decimal("120"), low=Decimal("99"))
        make_kline(DAY + timedelta(days=1), close=Decimal("95"), high=Decimal("101"), low=Decimal("80"))

        run = scan(DAY, DAY + timedelta(days=2))

        self.assertEqual(run.anomaly_day_count, 2)
        self.assertEqual(run.signal_count, 4)

    def test_missing_days_are_counted(self):
        make_kline(DAY)
        make_kline(DAY + timedelta(days=2))

        run = scan(DAY, DAY + timedelta(days=3))

        self.assertEqual(run.expected_count, 3)
        self.assertEqual(run.actual_count, 2)
        self.assertEqual(run.missing_count, 1)

    def test_unsafe_current_data_is_skipped(self):
        make_kline(DAY, open=Decimal("0"), close=Decimal("0"), high=Decimal("1"), low=Decimal("0"))

        run = scan()

        self.assertEqual(run.actual_count, 1)
        self.assertEqual(run.evaluated_count, 0)
        self.assertEqual(run.skipped_invalid_count, 1)

    def test_no_anomaly_is_still_success(self):
        make_kline(DAY)

        run = scan()

        self.assertEqual(run.status, MarketScanRun.Status.SUCCESS)
        self.assertEqual(run.anomaly_day_count, 0)

    @patch("apps.market_monitoring.services._load_klines")
    def test_service_exception_is_saved_as_failed_without_exception_text(self, load):
        load.side_effect = RuntimeError("secret HTTP response body")

        run = scan()

        self.assertEqual(run.status, MarketScanRun.Status.FAILED)
        self.assertIn("RuntimeError", run.error_message)
        self.assertNotIn("secret HTTP response body", run.error_message)
        self.assertIsNotNone(run.finished_at)

    def test_rules_snapshot_contains_all_v1_thresholds(self):
        make_kline(DAY)

        snapshot = scan().rules_snapshot

        self.assertEqual(snapshot["version"], "v1")
        self.assertEqual(snapshot["price_change"]["absolute_threshold_pct"], str(PRICE_CHANGE_THRESHOLD_PCT))
        self.assertEqual(snapshot["volume_spike"]["baseline_days"], 20)
        self.assertEqual(snapshot["volume_spike"]["ratio_threshold"], str(VOLUME_RATIO_THRESHOLD))
        self.assertEqual(snapshot["long_wick"]["body_ratio_threshold"], str(WICK_BODY_RATIO_THRESHOLD))
        self.assertEqual(snapshot["long_wick"]["range_ratio_threshold"], str(WICK_RANGE_RATIO_THRESHOLD))

    def test_finding_keeps_metric_snapshot_after_kline_changes(self):
        kline = make_kline(DAY, close=Decimal("105"), high=Decimal("106"), low=Decimal("99"))
        finding = scan().findings.get()

        kline.close = Decimal("120")
        kline.save(update_fields=["close", "updated_at"])
        finding.refresh_from_db()

        self.assertEqual(finding.close, Decimal("105"))
        self.assertEqual(finding.price_change_pct, Decimal("5"))
        self.assertEqual(finding.signals[0]["threshold"]["value"], "5")

    def test_same_run_and_day_cannot_be_saved_twice(self):
        kline = make_kline(DAY, close=Decimal("105"), high=Decimal("106"), low=Decimal("99"))
        run = scan()
        original = run.findings.get()

        with self.assertRaises(IntegrityError), transaction.atomic():
            MarketAnomalyFinding.objects.create(
                run=run,
                kline=kline,
                open_time=DAY,
                open=kline.open,
                high=kline.high,
                low=kline.low,
                close=kline.close,
                volume=kline.volume,
                price_change_pct=original.price_change_pct,
                amplitude_pct=original.amplitude_pct,
                upper_wick_range_ratio=original.upper_wick_range_ratio,
                lower_wick_range_ratio=original.lower_wick_range_ratio,
                signals=original.signals,
            )

    def test_scan_uses_single_loader_call_for_range_and_baseline(self):
        make_baseline()
        make_kline(DAY)

        with patch(
            "apps.market_monitoring.services._load_klines",
            wraps=__import__(
                "apps.market_monitoring.services", fromlist=["_load_klines"]
            )._load_klines,
        ) as load:
            scan()

        load.assert_called_once_with(DAY, DAY + timedelta(days=1))

    def test_signals_have_required_structure_and_decimal_strings(self):
        make_kline(DAY, close=Decimal("105"), high=Decimal("106"), low=Decimal("99"))

        signal = scan().findings.get().signals[0]

        self.assertEqual(set(signal), {"type", "direction", "metric", "threshold"})
        self.assertIsInstance(signal["metric"]["value"], str)
        self.assertIsInstance(signal["threshold"]["value"], str)

    def test_rules_snapshot_factory_returns_independent_complete_objects(self):
        first = rules_snapshot()
        second = rules_snapshot()
        first["price_change"]["absolute_threshold_pct"] = "999"

        self.assertEqual(second["price_change"]["absolute_threshold_pct"], "5")
