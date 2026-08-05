import json
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from apps.market_funds.parsers import (
    UpstreamStructureError,
    parse_defillama_chart,
    parse_etherscan_accounts_html,
    parse_etf_flow_value,
    parse_farside_html,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ParserTests(SimpleTestCase):
    def test_defillama_fixture_parses_total_supply(self):
        records = parse_defillama_chart((FIXTURES / "defillama_chart.json").read_text())
        self.assertEqual(len(records), 3)
        self.assertEqual(records[-1].circulating_supply_usd, Decimal("121"))
        self.assertIsNone(records[-1].bridged_supply_usd)

    def test_defillama_schema_change_fails_safely(self):
        with self.assertRaises(UpstreamStructureError):
            parse_defillama_chart([{"date": 1, "unexpected": {}}])

    def test_farside_fixture_discovers_tickers_and_total(self):
        records = parse_farside_html((FIXTURES / "farside_eth.html").read_text())
        self.assertEqual({item.ticker for item in records}, {"ETHA", "FETH", "TOTAL"})
        self.assertEqual(len(records), 9)

    def test_etf_value_preserves_negative_zero_missing_and_empty(self):
        self.assertEqual(parse_etf_flow_value("(2.5)"), Decimal("-2500000.0"))
        self.assertEqual(parse_etf_flow_value("0.0"), Decimal("0.0"))
        self.assertIsNone(parse_etf_flow_value("-"))
        self.assertIsNone(parse_etf_flow_value(""))

    def test_farside_schema_change_fails_safely(self):
        with self.assertRaises(UpstreamStructureError):
            parse_farside_html("<table><tr><td>changed</td></tr></table>")

    def test_etherscan_fixture_parses_full_address_from_link(self):
        records = parse_etherscan_accounts_html(
            (FIXTURES / "etherscan_accounts.html").read_text()
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].balance_eth, Decimal("1234.500000"))
        self.assertEqual(records[0].public_label, "Public Label")
