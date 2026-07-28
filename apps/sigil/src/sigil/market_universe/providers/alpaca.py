"""Governed Alpaca asset catalog ingestion with explicit evidence outcomes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sigil.market_universe.models import SourceInstrument

PROVIDER_SCHEMA = "alpaca-trading-assets-v2-2026-06"
SUPPORTED_EXCHANGES = frozenset({"AMEX", "ARCA", "BATS", "NASDAQ", "NYSE", "NYSEARCA"})
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$", re.ASCII)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass(frozen=True, slots=True)
class AssetExclusion:
    source_index: int
    asset_id: str | None
    symbol: str | None
    reason: str
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class AssetCatalogResult:
    observed_at: str
    source_count: int
    accepted: tuple[SourceInstrument, ...]
    excluded: tuple[AssetExclusion, ...]
    conflict_count: int


class AlpacaAssetCatalogProvider:
    """Normalize every provider row or record an explicit exclusion."""

    def __init__(self, *, audit: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self._audit = audit or (lambda _event, _details: None)

    def ingest(
        self, records: Iterable[object], *, observed_at: str | None = None
    ) -> AssetCatalogResult:
        timestamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rows = tuple(records)
        accepted: list[SourceInstrument] = []
        excluded: list[AssetExclusion] = []
        seen_ids: set[str] = set()
        seen_symbols: dict[str, str] = {}
        conflicts = 0
        for index, raw in enumerate(rows):
            digest = hashlib.sha256(_canonical(raw)).hexdigest()
            reason: str | None = None
            row = raw if isinstance(raw, dict) else {}
            asset_id = row.get("id") if isinstance(row.get("id"), str) else None
            symbol = row.get("symbol") if isinstance(row.get("symbol"), str) else None
            normalized_symbol = symbol.strip().upper() if symbol else None
            asset_class = row.get("class")
            status = row.get("status")
            exchange = str(row.get("exchange", "")).upper()
            if not isinstance(raw, dict):
                reason = "insufficient_provider_evidence"
            elif not asset_id or not asset_id.strip():
                reason = "missing_asset_id"
            elif asset_id in seen_ids:
                reason = "duplicate_asset"
            elif asset_class != "us_equity":
                reason = "unsupported_asset_class"
            elif status == "inactive":
                reason = "inactive"
            elif status != "active":
                reason = "unsupported_status"
            elif exchange not in SUPPORTED_EXCHANGES:
                reason = "unsupported_exchange"
            elif not normalized_symbol or not SYMBOL.fullmatch(normalized_symbol):
                reason = "malformed_symbol"
            elif normalized_symbol in seen_symbols and seen_symbols[normalized_symbol] != asset_id:
                reason = "identity_conflict"
                conflicts += 1
            elif normalized_symbol.startswith("TEST") or bool(row.get("test_asset")):
                reason = "test_asset"
            if reason:
                item = AssetExclusion(index, asset_id, normalized_symbol, reason, digest)
                excluded.append(item)
                self._audit("alpaca_asset_excluded", {"reason": reason, "asset_id": asset_id})
                continue
            assert asset_id is not None and normalized_symbol is not None
            seen_ids.add(asset_id)
            seen_symbols[normalized_symbol] = asset_id
            attributes = row.get("attributes")
            asset_type = (
                "etf"
                if isinstance(attributes, list) and any(str(value).lower() == "etf" for value in attributes)
                else "equity"
            )
            accepted.append(
                SourceInstrument(
                    source_id="ALPACA",
                    source_record_id=asset_id,
                    observed_at=timestamp,
                    symbol=normalized_symbol,
                    name=str(row.get("name") or normalized_symbol),
                    exchange=exchange,
                    asset_class=asset_type,
                    status="active",
                    broker_tradable=bool(row.get("tradable")),
                    provider_asset_class=str(asset_class),
                    provider_status=str(status),
                    fractionable=bool(row.get("fractionable")),
                    marginable=bool(row.get("marginable")),
                    shortable=bool(row.get("shortable")),
                    easy_to_borrow=bool(row.get("easy_to_borrow")),
                    borrow_status=(
                        str(row["borrow_status"]) if row.get("borrow_status") is not None else None
                    ),
                    maintenance_margin_requirement=(
                        str(row["maintenance_margin_requirement"])
                        if row.get("maintenance_margin_requirement") is not None
                        else None
                    ),
                    provider_schema=PROVIDER_SCHEMA,
                    raw_evidence_digest=digest,
                )
            )
        return AssetCatalogResult(timestamp, len(rows), tuple(accepted), tuple(excluded), conflicts)
