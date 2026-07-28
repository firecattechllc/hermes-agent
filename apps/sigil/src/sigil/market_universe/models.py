"""Immutable contracts for the governed market universe."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class UniverseValidationError(ValueError):
    """Raised when universe evidence or persisted state fails closed."""


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    ADR = "adr"
    REIT = "reit"
    OTHER = "other"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    HALTED = "halted"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class ReconciliationStatus(StrEnum):
    VALIDATED = "validated"
    CONFLICTED = "conflicted"
    EXCLUDED = "excluded"


class MonitoringTier(StrEnum):
    PROPOSAL_ELIGIBLE = "proposal_eligible"
    ACTIVELY_RESEARCHED = "actively_researched"
    BROKER_TRADABLE = "broker_tradable"
    MASTER_ONLY = "master_only"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    source_id: str
    source_record_id: str
    observed_at: str
    digest: str


@dataclass(frozen=True, slots=True)
class SourceInstrument:
    source_id: str
    source_record_id: str
    observed_at: str
    symbol: str
    name: str
    exchange: str
    currency: str = "USD"
    country: str = "US"
    asset_class: str = "equity"
    status: str = "active"
    broker_tradable: bool = False
    actively_researched: bool = False
    proposal_eligible: bool = False
    aliases: tuple[str, ...] = ()
    figi: str | None = None
    isin: str | None = None
    cusip: str | None = None
    sector: str | None = None
    industry: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalInstrument:
    instrument_id: str
    symbol: str
    name: str
    exchange: str
    currency: str
    country: str
    asset_class: str
    lifecycle_status: str
    reconciliation_status: str
    monitoring_tier: str
    aliases: tuple[str, ...]
    figi: str | None
    isin: str | None
    cusip: str | None
    sector: str | None
    industry: str | None
    broker_tradable: bool
    actively_researched: bool
    proposal_eligible: bool
    exclusion_reasons: tuple[str, ...]
    conflict_fields: tuple[str, ...]
    evidence: tuple[SourceEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    schema_version: int
    policy_version: str
    generated_at: str
    source_record_count: int
    instruments: tuple[CanonicalInstrument, ...]
    snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "generated_at": self.generated_at,
            "source_record_count": self.source_record_count,
            "instruments": [item.to_dict() for item in self.instruments],
            "snapshot_id": self.snapshot_id,
        }
