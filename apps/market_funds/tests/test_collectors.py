from pathlib import Path

import httpx
from django.test import TestCase, override_settings

from apps.collection.models import CollectionRun
from apps.market_funds.collectors import (
    collect_address_balances,
    collect_etf_flows,
    collect_stablecoin_supply,
)
from apps.market_funds.http import ResilientHttpClient
from apps.market_funds.models import EtfFlowDaily, SourceDiagnostic, StablecoinSupplyDaily


FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, content, content_type):
        self.content = content.encode()
        self.content_type = content_type
        self.request_count = 0

    def get(self, url):
        self.request_count += 1
        return httpx.Response(
            200,
            content=self.content,
            headers={"content-type": self.content_type},
            request=httpx.Request("GET", url),
        )


class CollectorTests(TestCase):
    def test_stablecoin_collection_is_idempotent_and_updates_revision(self):
        payload = (FIXTURES / "defillama_chart.json").read_text()
        first = collect_stablecoin_supply(client=FakeClient(payload, "application/json"))
        second = collect_stablecoin_supply(client=FakeClient(payload, "application/json"))
        self.assertEqual(first.status, CollectionRun.Status.SUCCESS)
        self.assertEqual(first.inserted_count, 3)
        self.assertEqual(second.skipped_count, 3)
        revised = payload.replace('"peggedUSD": 121', '"peggedUSD": 125')
        third = collect_stablecoin_supply(client=FakeClient(revised, "application/json"))
        self.assertEqual(third.updated_count, 1)
        self.assertEqual(StablecoinSupplyDaily.objects.count(), 3)

    def test_etf_repeat_collection_with_no_new_rows_is_success(self):
        html = (FIXTURES / "farside_eth.html").read_text()
        first = collect_etf_flows(client=FakeClient(html, "text/html"))
        second = collect_etf_flows(client=FakeClient(html, "text/html"))
        self.assertEqual(first.inserted_count, 9)
        self.assertEqual(second.status, CollectionRun.Status.SUCCESS)
        self.assertEqual(second.skipped_count, 9)
        self.assertEqual(EtfFlowDaily.objects.get(ticker="FETH", trade_date="2026-08-01").flow_usd, -2500000)
        self.assertIsNone(EtfFlowDaily.objects.get(ticker="FETH", trade_date="2026-08-02").flow_usd)

    def test_structure_error_records_safe_failure_without_body(self):
        run = collect_etf_flows(client=FakeClient("secret upstream body", "text/html"))
        self.assertEqual(run.status, CollectionRun.Status.FAILED)
        self.assertNotIn("secret upstream body", run.error_message)

    def test_address_collector_stops_at_terms_policy_gate(self):
        run = collect_address_balances()
        self.assertEqual(run.request_count, 0)
        self.assertEqual(run.status, CollectionRun.Status.FAILED)
        self.assertIn("SourcePolicyBlocked", run.error_message)
        self.assertEqual(SourceDiagnostic.objects.get(source="Etherscan").policy_status, "blocked")

    @override_settings(MARKET_FUNDS_MAX_RETRIES=2)
    def test_retry_is_bounded_for_429_and_5xx(self):
        calls = []

        def handler(request):
            calls.append(request)
            status = 429 if len(calls) == 1 else 503 if len(calls) == 2 else 200
            return httpx.Response(status, json={"ok": True})

        client = ResilientHttpClient(
            transport=httpx.MockTransport(handler), sleep=lambda seconds: None
        )
        response = client.get("https://example.test/data")
        client.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 3)
