"""Deterministic normalization and reconciliation for market instruments."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import replace
from typing import Iterable

from .models import (
    AssetClass,
    CanonicalInstrument,
    LifecycleStatus,
    MonitoringTier,
    ReconciliationStatus,
    SourceEvidence,
    SourceInstrument,
    UniverseSnapshot,
    UniverseValidationError,
)

POLICY_VERSION = "sigil-market-universe-v1"
SCHEMA_VERSION = 1
TARGET_MINIMUM = 8_000
TARGET_MAXIMUM = 12_000
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$", re.ASCII)
IDENTIFIER = re.compile(r"^[A-Z0-9][A-Z0-9.\-_:]{0,63}$", re.ASCII)
SUPPORTED_CURRENCIES = frozenset({"USD", "CAD"})
SUPPORTED_COUNTRIES = frozenset({"US", "CA"})
PROPOSAL_ASSET_CLASSES = frozenset({AssetClass.EQUITY.value, AssetClass.ETF.value, AssetClass.ADR.value, AssetClass.REIT.value})


def _text(value: str, label: str, maximum: int = 200) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > maximum:
        raise UniverseValidationError(f"{label} is invalid")
    return normalized


def _code(value: str, label: str) -> str:
    normalized = value.strip().upper()
    if not IDENTIFIER.fullmatch(normalized):
        raise UniverseValidationError(f"{label} is invalid")
    return normalized


def _digest(record: SourceInstrument) -> str:
    payload = {
        field: getattr(record, field)
        for field in record.__dataclass_fields__
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_source(record: SourceInstrument) -> SourceInstrument:
    symbol = _code(record.symbol, "symbol")
    if not SYMBOL.fullmatch(symbol):
        raise UniverseValidationError("symbol is invalid")
    asset_class = record.asset_class.strip().lower()
    status = record.status.strip().lower()
    if asset_class not in {item.value for item in AssetClass}:
        raise UniverseValidationError("asset_class is invalid")
    if status not in {item.value for item in LifecycleStatus}:
        raise UniverseValidationError("status is invalid")
    currency = _code(record.currency, "currency")
    country = _code(record.country, "country")
    if currency not in SUPPORTED_CURRENCIES or country not in SUPPORTED_COUNTRIES:
        raise UniverseValidationError("instrument geography is outside policy")
    aliases = tuple(
        sorted(
            {
                _code(alias, "alias")
                for alias in (*record.aliases, symbol)
            }
        )
    )
    return replace(
        record,
        source_id=_code(record.source_id, "source_id"),
        source_record_id=_code(record.source_record_id, "source_record_id"),
        observed_at=_text(record.observed_at, "observed_at", 40),
        symbol=symbol,
        name=_text(record.name, "name"),
        exchange=_code(record.exchange, "exchange"),
        currency=currency,
        country=country,
        asset_class=asset_class,
        status=status,
        aliases=aliases,
        figi=_code(record.figi, "figi") if record.figi else None,
        isin=_code(record.isin, "isin") if record.isin else None,
        cusip=_code(record.cusip, "cusip") if record.cusip else None,
        sector=_text(record.sector, "sector") if record.sector else None,
        industry=_text(record.industry, "industry") if record.industry else None,
    )


def _identity(record: SourceInstrument) -> str:
    if record.figi:
        return f"FIGI:{record.figi}"
    if record.isin:
        return f"ISIN:{record.isin}"
    if record.cusip:
        return f"CUSIP:{record.cusip}"
    return f"LISTING:{record.country}:{record.exchange}:{record.symbol}"


def _instrument_id(identity: str) -> str:
    return f"SIGIL-{hashlib.sha256(identity.encode()).hexdigest()[:20].upper()}"


def reconcile_sources(
    records: Iterable[SourceInstrument],
    *,
    generated_at: str,
) -> UniverseSnapshot:
    normalized = tuple(normalize_source(record) for record in records)
    if not normalized:
        raise UniverseValidationError("at least one source record is required")
    seen_source_records: set[tuple[str, str]] = set()
    groups: dict[str, list[SourceInstrument]] = defaultdict(list)
    for record in normalized:
        source_key = (record.source_id, record.source_record_id)
        if source_key in seen_source_records:
            raise UniverseValidationError("duplicate source record identity")
        seen_source_records.add(source_key)
        groups[_identity(record)].append(record)

    instruments: list[CanonicalInstrument] = []
    conflict_attributes = (
        "symbol", "name", "exchange", "currency", "country", "asset_class",
        "figi", "isin", "cusip",
    )
    for identity, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda item: (item.source_id, item.source_record_id))
        conflicts = tuple(
            field
            for field in conflict_attributes
            if len({getattr(item, field) for item in ordered if getattr(item, field) is not None}) > 1
        )
        statuses = {item.status for item in ordered}
        lifecycle = (
            LifecycleStatus.DELISTED.value
            if LifecycleStatus.DELISTED.value in statuses
            else LifecycleStatus.HALTED.value
            if LifecycleStatus.HALTED.value in statuses
            else LifecycleStatus.ACTIVE.value
            if statuses == {LifecycleStatus.ACTIVE.value}
            else LifecycleStatus.UNKNOWN.value
        )
        base = ordered[0]
        exclusion_reasons: list[str] = []
        if conflicts:
            exclusion_reasons.append("unresolved_source_conflict")
        if lifecycle != LifecycleStatus.ACTIVE.value:
            exclusion_reasons.append(f"lifecycle_{lifecycle}")
        if base.asset_class == AssetClass.OTHER.value:
            exclusion_reasons.append("unsupported_asset_class")
        broker = all(item.broker_tradable for item in ordered) and not exclusion_reasons
        researched = any(item.actively_researched for item in ordered) and not exclusion_reasons
        proposal_requested = any(item.proposal_eligible for item in ordered)
        proposal = (
            proposal_requested
            and broker
            and researched
            and base.asset_class in PROPOSAL_ASSET_CLASSES
            and not exclusion_reasons
        )
        reconciliation = (
            ReconciliationStatus.CONFLICTED.value
            if conflicts
            else ReconciliationStatus.EXCLUDED.value
            if exclusion_reasons
            else ReconciliationStatus.VALIDATED.value
        )
        tier = (
            MonitoringTier.EXCLUDED.value
            if exclusion_reasons
            else MonitoringTier.PROPOSAL_ELIGIBLE.value
            if proposal
            else MonitoringTier.ACTIVELY_RESEARCHED.value
            if researched
            else MonitoringTier.BROKER_TRADABLE.value
            if broker
            else MonitoringTier.MASTER_ONLY.value
        )
        evidence = tuple(
            SourceEvidence(
                source_id=item.source_id,
                source_record_id=item.source_record_id,
                observed_at=item.observed_at,
                digest=_digest(item),
            )
            for item in ordered
        )
        instruments.append(
            CanonicalInstrument(
                instrument_id=_instrument_id(identity),
                symbol=base.symbol,
                name=base.name,
                exchange=base.exchange,
                currency=base.currency,
                country=base.country,
                asset_class=base.asset_class,
                lifecycle_status=lifecycle,
                reconciliation_status=reconciliation,
                monitoring_tier=tier,
                aliases=tuple(sorted({alias for item in ordered for alias in item.aliases})),
                figi=base.figi,
                isin=base.isin,
                cusip=base.cusip,
                sector=base.sector,
                industry=base.industry,
                broker_tradable=broker,
                actively_researched=researched,
                proposal_eligible=proposal,
                exclusion_reasons=tuple(sorted(set(exclusion_reasons))),
                conflict_fields=conflicts,
                evidence=evidence,
            )
        )
    canonical = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "generated_at": generated_at,
        "source_record_count": len(normalized),
        "instruments": [item.to_dict() for item in instruments],
    }
    snapshot_id = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return UniverseSnapshot(
        schema_version=SCHEMA_VERSION,
        policy_version=POLICY_VERSION,
        generated_at=generated_at,
        source_record_count=len(normalized),
        instruments=tuple(instruments),
        snapshot_id=snapshot_id,
    )
