"""Versioned policy for governed dossier construction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta

from sigil.accounting.models import (
    canonical_digest,
    decimal_text,
    identifier,
    reject_secret_bearing,
)
from sigil.integrations.providers.models import FinancialDataValidationError

from .models import (
    SUPPORTED_CURRENCIES,
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_SECTIONS,
)

SUPPORTED_FILINGS = frozenset({"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A"})


@dataclass(frozen=True, slots=True)
class ResearchDossierPolicy:
    version: str = "sigil-dossier-policy-v1"
    maximum_evidence_age: tuple[tuple[str, timedelta], ...] = ()
    maximum_filing_age: timedelta = timedelta(days=550)
    maximum_market_data_age: timedelta = timedelta(days=2)
    maximum_sentiment_age: timedelta = timedelta(days=30)
    maximum_portfolio_context_age: timedelta = timedelta(days=2)
    maximum_risk_context_age: timedelta = timedelta(days=2)
    allowed_future_clock_skew: timedelta = timedelta(minutes=5)
    minimum_financial_history_periods: int = 2
    minimum_filing_coverage: int = 1
    minimum_evidence_per_required_section: int = 1
    maximum_evidence_per_section: int = 1_000
    maximum_total_evidence: int = 10_000
    maximum_conflicts_for_high_confidence: int = 0
    maximum_unresolved_material_gaps: int = 0
    maximum_construction_duration: timedelta = timedelta(minutes=2)
    maximum_narrative_length: int = 4_000
    supported_currencies: tuple[str, ...] = ("USD",)
    supported_instruments: tuple[str, ...] = ("EQUITY", "ETF")
    supported_filing_types: tuple[str, ...] = ("10-K", "10-Q", "8-K")
    required_sections: tuple[str, ...] = (
        "identity",
        "business_profile",
        "financial_history",
        "filings",
        "risk_factors",
    )
    optional_sections: tuple[str, ...] = (
        "management",
        "governance",
        "valuation",
        "sentiment",
        "portfolio_relevance",
        "risk_relevance",
        "competitive_positioning",
        "industry_context",
    )
    materiality_thresholds: tuple[tuple[str, str], ...] = ()
    numeric_comparison_tolerances: tuple[tuple[str, str], ...] = ()
    policy_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.version, "policy version")
        if any(
            marker in self.version.casefold()
            for marker in ("authorization", "api_key", "access_token", "private_key")
        ):
            raise FinancialDataValidationError("secret-bearing dossier policy is forbidden")
        durations = (
            self.maximum_filing_age,
            self.maximum_market_data_age,
            self.maximum_sentiment_age,
            self.maximum_portfolio_context_age,
            self.maximum_risk_context_age,
            self.allowed_future_clock_skew,
            self.maximum_construction_duration,
            *(duration for _, duration in self.maximum_evidence_age),
        )
        if any(not isinstance(value, timedelta) or value < timedelta(0) for value in durations):
            raise FinancialDataValidationError("policy age limit is invalid")
        counts = (
            self.minimum_financial_history_periods,
            self.minimum_filing_coverage,
            self.minimum_evidence_per_required_section,
            self.maximum_evidence_per_section,
            self.maximum_total_evidence,
            self.maximum_conflicts_for_high_confidence,
            self.maximum_unresolved_material_gaps,
            self.maximum_narrative_length,
        )
        if any(isinstance(value, bool) or value < 0 for value in counts):
            raise FinancialDataValidationError("policy count is invalid")
        if (
            self.maximum_evidence_per_section > 10_000
            or self.maximum_total_evidence > 100_000
            or self.maximum_narrative_length > 100_000
        ):
            raise FinancialDataValidationError("policy limit exceeds governance bound")
        if self.minimum_evidence_per_required_section > self.maximum_evidence_per_section:
            raise FinancialDataValidationError("contradictory evidence requirements")
        required = tuple(sorted(self.required_sections))
        optional = tuple(sorted(self.optional_sections))
        if len(set(required)) != len(required) or len(set(optional)) != len(optional):
            raise FinancialDataValidationError("duplicate section identifier")
        if not set(required + optional) <= SUPPORTED_SECTIONS:
            raise FinancialDataValidationError("unsupported section identifier")
        if set(required) & set(optional):
            raise FinancialDataValidationError("contradictory required and optional sections")
        if not set(self.supported_currencies) <= SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")
        if not set(self.supported_instruments) <= SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("unsupported instrument")
        if not set(self.supported_filing_types) <= SUPPORTED_FILINGS:
            raise FinancialDataValidationError("unsupported filing type")
        object.__setattr__(self, "required_sections", required)
        object.__setattr__(self, "optional_sections", optional)
        object.__setattr__(self, "supported_currencies", tuple(sorted(self.supported_currencies)))
        object.__setattr__(self, "supported_instruments", tuple(sorted(self.supported_instruments)))
        object.__setattr__(
            self, "supported_filing_types", tuple(sorted(self.supported_filing_types))
        )
        for name in ("materiality_thresholds", "numeric_comparison_tolerances"):
            values = tuple(sorted(getattr(self, name)))
            if len({key for key, _ in values}) != len(values):
                raise FinancialDataValidationError(f"duplicate {name}")
            for key, value in values:
                identifier(key, name)
                decimal_text(value, name)
            object.__setattr__(self, name, values)
        reject_secret_bearing(asdict(self))
        material = {
            key: (
                value.total_seconds()
                if isinstance(value, timedelta)
                else [
                    [item_key, item_value.total_seconds()]
                    for item_key, item_value in value
                ]
                if key == "maximum_evidence_age"
                else value
            )
            for key, value in asdict(self).items()
            if key != "policy_identity"
        }
        computed = canonical_digest(material)
        if self.policy_identity and self.policy_identity != computed:
            raise FinancialDataValidationError("policy_identity mismatch")
        object.__setattr__(self, "policy_identity", computed)
