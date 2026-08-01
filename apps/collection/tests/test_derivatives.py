from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.collection.derivatives import collect_funding_rates, collect_open_interest
from apps.collection.models import CollectionRun
from apps.market_data.derivatives import FundingRatePayload, OpenInterestPayload
from apps.market_data.models import FundingRate, OpenInterest


START = datetime(2026, 7, 1, tzinfo=UTC)


class FakeClient:
    def __init__(self, batches, error=None):
        self.batches = batches
        self.error = error
        self.request_count = len(batches)
        self.received_count = sum(map(len, batches))
        self.skipped_count = 0

    def iter_batches(self, **kwargs):
        yield from self.batches
        if self.error:
            raise self.error


class DerivativesCollectionTests(TestCase):
    def test_oi_duplicate_is_idempotent_and_decimal_is_exact(self):
        payload = OpenInterestPayload(
            START,
            Decimal("1234.123456789012345678"),
            Decimal("4567890.123456789012345678"),
        )
        first = collect_open_interest("ETHUSDT", START, START + timedelta(days=1), client=FakeClient([[payload]]))
        second = collect_open_interest("ETHUSDT", START, START + timedelta(days=1), client=FakeClient([[payload]]))

        self.assertEqual(first.inserted_count, 1)
        self.assertEqual(second.skipped_count, 1)
        self.assertEqual(OpenInterest.objects.count(), 1)
        self.assertEqual(OpenInterest.objects.get().sum_open_interest, payload.sum_open_interest)

    def test_funding_duplicate_updates_only_when_value_changes(self):
        original = FundingRatePayload(START, Decimal("0.0001"), Decimal("3000.123456789012345678"), "")
        changed = FundingRatePayload(START, Decimal("0.0002"), original.mark_price, "")
        collect_funding_rates("ETHUSDT", START, START + timedelta(days=1), client=FakeClient([[original]]))
        unchanged = collect_funding_rates("ETHUSDT", START, START + timedelta(days=1), client=FakeClient([[original]]))
        updated = collect_funding_rates("ETHUSDT", START, START + timedelta(days=1), client=FakeClient([[changed]]))

        self.assertEqual(unchanged.skipped_count, 1)
        self.assertEqual(updated.updated_count, 1)
        self.assertEqual(FundingRate.objects.count(), 1)
        self.assertEqual(FundingRate.objects.get().funding_rate, Decimal("0.0002"))

    def test_failure_records_failed_count_and_safe_summary(self):
        run = collect_open_interest(
            "ETHUSDT", START, START + timedelta(days=1),
            client=FakeClient([], RuntimeError("request stopped")),
        )

        self.assertEqual(run.status, CollectionRun.Status.FAILED)
        self.assertEqual(run.failed_count, 1)
        self.assertIn("request stopped", run.error_message)


class DerivativesCollectionViewTests(TestCase):
    def test_get_shows_manual_oi_and_funding_controls(self):
        response = self.client.get(reverse("collection:derivatives"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OI（1h）")
        self.assertContains(response, "Funding（实际结算）")
        self.assertContains(response, "最近 20 次 OI / Funding 采集")
        self.assertContains(
            response,
            '<details class="nav-group is-active" data-nav-group="derivatives" open>',
        )
        self.assertContains(
            response,
            '<a class="nav-subitem is-active" href="/collection/derivatives/" aria-current="page">数据采集</a>',
        )

    @patch("apps.collection.views.collect_and_inspect")
    def test_post_runs_selected_collectors_with_quality_checks(self, collect):
        collect.side_effect = [
            SimpleNamespace(
                collection_run=SimpleNamespace(status=CollectionRun.Status.SUCCESS),
                inspection_run=SimpleNamespace(status="success", quality_status="passed"),
            ),
            SimpleNamespace(
                collection_run=SimpleNamespace(status=CollectionRun.Status.SUCCESS),
                inspection_run=SimpleNamespace(status="success", quality_status="passed"),
            ),
        ]

        response = self.client.post(
            reverse("collection:derivatives"),
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-02",
                "data_types": ["open_interest", "funding"],
            },
        )

        self.assertRedirects(response, reverse("collection:derivatives"))
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(collect.call_args_list[0].kwargs["data_type"], "open_interest")
        self.assertEqual(collect.call_args_list[1].kwargs["data_type"], "funding")
