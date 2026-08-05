from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


class UpstreamStructureError(ValueError):
    pass


def _decimal(value, *, field: str, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UpstreamStructureError(f"invalid numeric field: {field}") from exc
    if not result.is_finite() or result < 0:
        raise UpstreamStructureError(f"invalid numeric field: {field}")
    return result


@dataclass(frozen=True)
class StablecoinRecord:
    observation_date: date
    circulating_supply: Decimal
    circulating_supply_usd: Decimal
    minted_supply_usd: Decimal | None
    bridged_supply_usd: Decimal | None = None


def parse_defillama_chart(payload: str | bytes | list[dict]) -> list[StablecoinRecord]:
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise UpstreamStructureError("DeFiLlama response is not valid JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise UpstreamStructureError("DeFiLlama chart must be a non-empty list")

    records = []
    previous_date = None
    for item in payload:
        if not isinstance(item, dict):
            raise UpstreamStructureError("DeFiLlama chart row must be an object")
        try:
            timestamp = int(item["date"])
            circulating = item["totalCirculating"]["peggedUSD"]
            circulating_usd = item["totalCirculatingUSD"]["peggedUSD"]
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamStructureError("DeFiLlama chart schema changed") from exc
        observation_date = datetime.fromtimestamp(timestamp, tz=UTC).date()
        if previous_date is not None and observation_date <= previous_date:
            raise UpstreamStructureError("DeFiLlama dates are not strictly increasing")
        previous_date = observation_date
        minted = item.get("totalMintedUSD")
        minted_value = minted.get("peggedUSD") if isinstance(minted, dict) else None
        records.append(
            StablecoinRecord(
                observation_date=observation_date,
                circulating_supply=_decimal(circulating, field="totalCirculating"),
                circulating_supply_usd=_decimal(
                    circulating_usd, field="totalCirculatingUSD"
                ),
                minted_supply_usd=_decimal(
                    minted_value, field="totalMintedUSD", nullable=True
                ),
            )
        )
    return records


@dataclass
class HtmlCell:
    text: str
    hrefs: list[str]


class HtmlTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[HtmlCell]]] = []
        self._table = None
        self._row = None
        self._cell_text = None
        self._cell_hrefs = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif self._table is not None and tag == "tr":
            self._row = []
        elif self._row is not None and tag in {"th", "td"}:
            self._cell_text = []
            self._cell_hrefs = []
        elif self._cell_text is not None and tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._cell_hrefs.append(href)

    def handle_data(self, data):
        if self._cell_text is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self._cell_text is not None:
            self._row.append(
                HtmlCell(" ".join("".join(self._cell_text).split()), self._cell_hrefs)
            )
            self._cell_text = None
            self._cell_hrefs = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


@dataclass(frozen=True)
class EtfFlowRecord:
    trade_date: date
    ticker: str
    flow_usd: Decimal | None
    raw_value: str
    is_total: bool = False


def parse_etf_flow_value(raw_value: str) -> Decimal | None:
    value = raw_value.strip()
    if value in {"", "-", "–", "—"}:
        return None
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1]
    value = value.replace(",", "").replace("$", "").replace("*", "").strip()
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise UpstreamStructureError(f"invalid ETF flow value: {raw_value[:30]}") from exc
    if not parsed.is_finite():
        raise UpstreamStructureError("non-finite ETF flow value")
    return -parsed * Decimal("1000000") if negative else parsed * Decimal("1000000")


def _parse_farside_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d %b %Y").date()
    except ValueError:
        return None


def parse_farside_html(html: str) -> list[EtfFlowRecord]:
    parser = HtmlTableParser()
    parser.feed(html)
    for table in parser.tables:
        texts = [[cell.text for cell in row] for row in table]
        header_index = next(
            (
                index
                for index, row in enumerate(texts)
                if len(row) >= 4
                and row[0] == ""
                and row[-1] == ""
                and all(re.fullmatch(r"[A-Z][A-Z0-9.]{1,9}", item or "") for item in row[1:-1])
            ),
            None,
        )
        if header_index is None:
            continue
        tickers = texts[header_index][1:-1]
        records = []
        seen_dates = set()
        for row in texts[header_index + 1 :]:
            if len(row) != len(tickers) + 2:
                continue
            trade_date = _parse_farside_date(row[0])
            if trade_date is None:
                continue
            if trade_date in seen_dates:
                raise UpstreamStructureError("duplicate Farside trade date")
            seen_dates.add(trade_date)
            for ticker, raw_value in zip(tickers, row[1:-1], strict=True):
                records.append(
                    EtfFlowRecord(
                        trade_date=trade_date,
                        ticker=ticker,
                        flow_usd=parse_etf_flow_value(raw_value),
                        raw_value=raw_value,
                    )
                )
            records.append(
                EtfFlowRecord(
                    trade_date=trade_date,
                    ticker="TOTAL",
                    flow_usd=parse_etf_flow_value(row[-1]),
                    raw_value=row[-1],
                    is_total=True,
                )
            )
        if records:
            return records
    raise UpstreamStructureError("Farside ETF flow table was not found")


@dataclass(frozen=True)
class AddressRecord:
    rank: int
    address: str
    public_label: str
    balance_eth: Decimal


ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")


def parse_etherscan_accounts_html(html: str) -> list[AddressRecord]:
    parser = HtmlTableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table or [cell.text for cell in table[0]][:4] != [
            "#",
            "Address",
            "Name Tag",
            "Balance",
        ]:
            continue
        records = []
        for row in table[1:]:
            if len(row) < 4 or not row[0].text.isdigit():
                continue
            address = next(
                (
                    match.group(0)
                    for href in row[1].hrefs
                    if (match := ADDRESS_RE.search(href))
                ),
                None,
            )
            if address is None:
                match = ADDRESS_RE.search(row[1].text)
                address = match.group(0) if match else None
            if address is None:
                raise UpstreamStructureError("Etherscan address is truncated without a link")
            balance_text = re.sub(r"\s*ETH\s*$", "", row[3].text, flags=re.I).replace(
                ",", ""
            )
            balance = _decimal(balance_text, field="balance_eth")
            records.append(
                AddressRecord(int(row[0].text), address.lower(), row[2].text, balance)
            )
        if not records:
            raise UpstreamStructureError("Etherscan account table contains no rows")
        ranks = [item.rank for item in records]
        if ranks != list(range(ranks[0], ranks[0] + len(ranks))):
            raise UpstreamStructureError("Etherscan account ranks are not contiguous")
        return records
    raise UpstreamStructureError("Etherscan account table was not found")
