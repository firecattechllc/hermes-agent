"""Versioned, deterministic policy for governed thesis construction."""

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

from .models import PROHIBITED_TERMS, SUPPORTED_CURRENCIES, SUPPORTED_INSTRUMENTS

THESIS_SECTIONS = frozenset(
    {
        "hypothesis",
        "pillars",
        "assumptions",
        "causal_chains",
        "catalysts",
        "risks",
        "invalidation_conditions",
        "falsification_tests",
        "monitoring_indicators",
        "expected_developments",
        "valuation_dependencies",
    }
)
COUNTER_SECTIONS = frozenset(
    {
        "hypothesis",
        "pillars",
        "alternative_explanations",
        "failure_mechanisms",
        "assumptions",
        "risks",
        "monitoring_indicators",
    }
)


@dataclass(frozen=True, slots=True)
class InvestmentThesisPolicy:
    version: str = "sigil-thesis-policy-v1"
    required_thesis_sections: tuple[str, ...] = tuple(sorted(THESIS_SECTIONS))
    required_counter_thesis_sections: tuple[str, ...] = tuple(sorted(COUNTER_SECTIONS))
    minimum_supporting_claims_per_pillar: int = 1
    minimum_contradicting_claims_per_pillar: int = 0
    minimum_counter_thesis_pillars: int = 1
    minimum_assumptions: int = 1
    minimum_invalidation_conditions: int = 1
    minimum_falsification_tests: int = 1
    minimum_monitoring_indicators: int = 1
    minimum_catalyst_coverage: int = 0
    minimum_risk_coverage: int = 1
    maximum_claims_per_argument: int = 50
    maximum_arguments_per_pillar: int = 20
    maximum_pillars: int = 20
    maximum_total_evidence_links: int = 2_000
    maximum_stale_evidence_age: tuple[tuple[str, timedelta], ...] = ()
    maximum_unresolved_material_conflicts: int = 0
    maximum_unresolved_material_gaps: int = 0
    maximum_unsupported_assumptions: int = 0
    maximum_narrative_length: int = 4_000
    maximum_construction_duration: timedelta = timedelta(minutes=2)
    maximum_thesis_time_horizon: timedelta = timedelta(days=3650)
    allowed_future_clock_skew: timedelta = timedelta(minutes=5)
    allowed_currencies: tuple[str, ...] = ("USD",)
    allowed_instruments: tuple[str, ...] = ("EQUITY", "ETF")
    allowed_conclusion_classifications: tuple[str, ...] = (
        "ready_for_review",
        "requires_research",
        "blocked",
        "unavailable",
    )
    prohibited_recommendation_terms: tuple[str, ...] = PROHIBITED_TERMS
    materiality_thresholds: tuple[tuple[str, str], ...] = ()
    policy_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.version, "policy version")
        if any(
            marker in self.version.casefold()
            for marker in ("api_key", "access_token", "authorization", "private_key")
        ):
            raise FinancialDataValidationError("secret-bearing thesis policy is forbidden")
        counts = (
            self.minimum_supporting_claims_per_pillar,
            self.minimum_contradicting_claims_per_pillar,
            self.minimum_counter_thesis_pillars,
            self.minimum_assumptions,
            self.minimum_invalidation_conditions,
            self.minimum_falsification_tests,
            self.minimum_monitoring_indicators,
            self.minimum_catalyst_coverage,
            self.minimum_risk_coverage,
            self.maximum_claims_per_argument,
            self.maximum_arguments_per_pillar,
            self.maximum_pillars,
            self.maximum_total_evidence_links,
            self.maximum_unresolved_material_conflicts,
            self.maximum_unresolved_material_gaps,
            self.maximum_unsupported_assumptions,
            self.maximum_narrative_length,
        )
        if any(isinstance(value, bool) or value < 0 for value in counts):
            raise FinancialDataValidationError("policy count is invalid")
        if any(value > 100_000 for value in counts):
            raise FinancialDataValidationError("policy limit exceeds governance bound")
        if self.minimum_counter_thesis_pillars == 0:
            raise FinancialDataValidationError("policy must require a counter-thesis")
        if self.minimum_invalidation_conditions == 0:
            raise FinancialDataValidationError("policy must require invalidation conditions")
        if self.minimum_supporting_claims_per_pillar == 0:
            raise FinancialDataValidationError("factual arguments require evidence")
        if (
            self.maximum_arguments_per_pillar == 0
            or self.maximum_pillars < self.minimum_counter_thesis_pillars
        ):
            raise FinancialDataValidationError("contradictory thesis policy")
        for field, supported in (
            ("required_thesis_sections", THESIS_SECTIONS),
            ("required_counter_thesis_sections", COUNTER_SECTIONS),
        ):
            values = tuple(sorted(getattr(self, field)))
            if not values or len(set(values)) != len(values):
                raise FinancialDataValidationError("empty or duplicate required section")
            if not set(values) <= supported:
                raise FinancialDataValidationError("unsupported required section")
            object.__setattr__(self, field, values)
        if not set(self.allowed_currencies) <= SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")
        if not set(self.allowed_instruments) <= SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("unsupported instrument")
        if not set(self.allowed_conclusion_classifications) <= {
            "ready_for_review",
            "requires_research",
            "blocked",
            "unavailable",
        }:
            raise FinancialDataValidationError("unsupported conclusion classification")
        durations = (
            self.maximum_construction_duration,
            self.maximum_thesis_time_horizon,
            self.allowed_future_clock_skew,
            *(value for _, value in self.maximum_stale_evidence_age),
        )
        if any(not isinstance(value, timedelta) or value <= timedelta(0) for value in durations):
            raise FinancialDataValidationError("policy duration is invalid")
        for key, value in self.materiality_thresholds:
            identifier(key, "materiality threshold")
            decimal_text(value, "materiality threshold", nonnegative=False)
        object.__setattr__(self, "allowed_currencies", tuple(sorted(self.allowed_currencies)))
        object.__setattr__(self, "allowed_instruments", tuple(sorted(self.allowed_instruments)))
        object.__setattr__(
            self,
            "prohibited_recommendation_terms",
            tuple(sorted(set(self.prohibited_recommendation_terms))),
        )
        reject_secret_bearing(asdict(self))
        material = {
            key: value.total_seconds()
            if isinstance(value, timedelta)
            else [[name, age.total_seconds()] for name, age in value]
            if key == "maximum_stale_evidence_age"
            else value
            for key, value in asdict(self).items()
            if key != "policy_identity"
        }
        computed = canonical_digest(material)
        if self.policy_identity and self.policy_identity != computed:
            raise FinancialDataValidationError("policy_identity mismatch")
        object.__setattr__(self, "policy_identity", computed)
