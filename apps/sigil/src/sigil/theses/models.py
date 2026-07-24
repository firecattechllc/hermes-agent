"""Immutable contracts for governed investment theses and counter-theses."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from sigil.accounting.models import (
    canonical_digest,
    decimal_text,
    digest,
    identifier,
    reject_secret_bearing,
    symbol,
    timestamp,
)
from sigil.integrations.providers.models import FinancialDataValidationError

SUPPORTED_INSTRUMENTS = frozenset({"EQUITY", "ETF"})
SUPPORTED_CURRENCIES = frozenset({"USD"})
ARGUMENT_TYPES = frozenset(
    {
        "business_quality",
        "revenue_growth",
        "margin_structure",
        "cash_generation",
        "balance_sheet",
        "capital_allocation",
        "dilution",
        "competitive_position",
        "management",
        "governance",
        "industry_structure",
        "valuation_dependency",
        "catalyst",
        "risk",
        "portfolio_relevance",
        "risk_relevance",
        "unresolved",
    }
)
MATERIALITIES = frozenset({"informational", "low", "moderate", "material", "critical"})
PROHIBITED_TERMS = (
    "strong buy",
    "strong sell",
    "target price",
    "price target",
    "fair value is",
    "guaranteed return",
    "guaranteed profit",
    "risk-free",
    "cannot lose",
    "reduce position",
    "position size",
    "enter trade",
    "exit trade",
    "overweight",
    "underweight",
    "accumulate",
    "allocate",
    "buy",
    "sell",
    "hold",
)


def _identity(instance: object, field: str) -> None:
    material = {key: value for key, value in asdict(instance).items() if key != field}
    computed = canonical_digest(material)
    supplied = getattr(instance, field)
    if supplied and supplied != computed:
        raise FinancialDataValidationError(f"{field} mismatch")
    object.__setattr__(instance, field, computed)


def _ids(values: tuple[str, ...], name: str, *, required: bool = False) -> tuple[str, ...]:
    ordered = tuple(sorted(values))
    if required and not ordered:
        raise FinancialDataValidationError(f"{name} is required")
    if len(set(ordered)) != len(ordered):
        raise FinancialDataValidationError(f"duplicate {name}")
    for value in ordered:
        identifier(value, name)
    return ordered


def _objects(values: tuple[object, ...], field: str, name: str) -> tuple[object, ...]:
    ordered = tuple(sorted(values, key=lambda item: getattr(item, field)))
    if len({getattr(item, field) for item in ordered}) != len(ordered):
        raise FinancialDataValidationError(f"duplicate {name}")
    return ordered


def _statement(value: str, name: str, *, maximum: int = 1_000) -> None:
    if not value.strip() or len(value) > maximum:
        raise FinancialDataValidationError(f"{name} is invalid")
    lowered = value.casefold()
    if any(
        re.search(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", lowered)
        for term in PROHIBITED_TERMS
    ):
        raise FinancialDataValidationError("prohibited recommendation language")
    reject_secret_bearing({name: value})


class ThesisArgumentType(StrEnum):
    BUSINESS_QUALITY = "business_quality"
    REVENUE_GROWTH = "revenue_growth"
    MARGIN_STRUCTURE = "margin_structure"
    CASH_GENERATION = "cash_generation"
    BALANCE_SHEET = "balance_sheet"
    CAPITAL_ALLOCATION = "capital_allocation"
    DILUTION = "dilution"
    COMPETITIVE_POSITION = "competitive_position"
    MANAGEMENT = "management"
    GOVERNANCE = "governance"
    INDUSTRY_STRUCTURE = "industry_structure"
    VALUATION_DEPENDENCY = "valuation_dependency"
    CATALYST = "catalyst"
    RISK = "risk"
    PORTFOLIO_RELEVANCE = "portfolio_relevance"
    RISK_RELEVANCE = "risk_relevance"
    UNRESOLVED = "unresolved"


class ThesisConfidenceClassification(StrEnum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class ThesisCompletenessClassification(StrEnum):
    COMPLETE = "complete"
    SUBSTANTIALLY_COMPLETE = "substantially_complete"
    PARTIAL = "partial"
    MATERIALLY_INCOMPLETE = "materially_incomplete"
    UNAVAILABLE = "unavailable"


class ThesisReadinessClassification(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    REQUIRES_RESEARCH = "requires_research"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class InvestmentThesisUnavailableReason(StrEnum):
    INVALID_DOSSIER = "invalid_dossier"
    UNRESOLVED_IDENTITY = "unresolved_identity"
    MATERIAL_CONFLICT = "material_conflict"
    MATERIAL_GAP = "material_gap"
    STALE_EVIDENCE = "stale_evidence"
    TRUNCATED_EVIDENCE = "truncated_evidence"
    MISSING_COUNTER_THESIS = "missing_counter_thesis"
    UNSUPPORTED_ASSUMPTION = "unsupported_assumption"
    MISSING_INVALIDATION = "missing_invalidation"
    MISSING_FALSIFICATION = "missing_falsification"
    INVALID_PROVENANCE = "invalid_provenance"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class InvestmentThesisSubject:
    dossier_identity: str
    issuer_id: str
    security_id: str | None
    ticker: str | None
    cik: str | None
    instrument_type: str
    currency: str
    thesis_horizon: str
    policy_identity: str
    subject_identity: str = ""

    def __post_init__(self) -> None:
        digest(self.dossier_identity, "dossier_identity")
        digest(self.policy_identity, "policy_identity")
        identifier(self.issuer_id, "issuer_id")
        if self.security_id is None or self.ticker is None:
            raise FinancialDataValidationError("ambiguous thesis subject")
        identifier(self.security_id, "security_id")
        object.__setattr__(self, "ticker", symbol(self.ticker))
        if self.instrument_type not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("unsupported instrument")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")
        if not self.thesis_horizon.strip():
            raise FinancialDataValidationError("thesis horizon is required")
        if self.cik is not None and (not self.cik.isdigit() or len(self.cik) != 10):
            raise FinancialDataValidationError("CIK is invalid")
        _identity(self, "subject_identity")


@dataclass(frozen=True, slots=True)
class ThesisEvidenceLink:
    claim_id: str
    claim_identity: str
    dossier_identity: str
    relationship: str
    stale: bool = False
    truncated: bool = False
    evidence_link_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.claim_id, "claim_id")
        digest(self.claim_identity, "claim_identity")
        digest(self.dossier_identity, "dossier_identity")
        if self.relationship not in {"supports", "contradicts", "context"}:
            raise FinancialDataValidationError("unsupported evidence relationship")
        _identity(self, "evidence_link_identity")


@dataclass(frozen=True, slots=True)
class ThesisAssumption:
    assumption_id: str
    statement: str
    category: str
    materiality: str
    supporting_claim_ids: tuple[str, ...] = ()
    contradicting_claim_ids: tuple[str, ...] = ()
    verification_status: str = "unsupported"
    monitorable: bool = True
    review_at: datetime | None = None
    assumption_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.assumption_id, "assumption_id")
        _statement(self.statement, "assumption statement")
        if self.category not in {
            "factual",
            "operational",
            "competitive",
            "financial",
            "regulatory",
            "management",
            "market",
            "valuation",
            "portfolio",
            "risk",
        }:
            raise FinancialDataValidationError("unsupported assumption category")
        if self.materiality not in MATERIALITIES:
            raise FinancialDataValidationError("unsupported materiality")
        if self.verification_status not in {"verified", "partially_supported", "unsupported"}:
            raise FinancialDataValidationError("unsupported verification status")
        object.__setattr__(
            self, "supporting_claim_ids", _ids(self.supporting_claim_ids, "assumption claims")
        )
        object.__setattr__(
            self,
            "contradicting_claim_ids",
            _ids(self.contradicting_claim_ids, "assumption contradictions"),
        )
        if self.review_at is not None:
            timestamp(self.review_at, "assumption review_at")
        _identity(self, "assumption_identity")


@dataclass(frozen=True, slots=True)
class ThesisDependency:
    dependency_id: str
    statement: str
    assumption_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    dependency_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.dependency_id, "dependency_id")
        _statement(self.statement, "dependency statement")
        object.__setattr__(
            self, "assumption_ids", _ids(self.assumption_ids, "dependency assumptions")
        )
        object.__setattr__(self, "claim_ids", _ids(self.claim_ids, "dependency claims"))
        _identity(self, "dependency_identity")


@dataclass(frozen=True, slots=True)
class ThesisExpectedDevelopment:
    development_id: str
    development_type: str
    statement: str
    earliest_observation: datetime
    latest_observation: datetime
    assumption_ids: tuple[str, ...]
    causal_chain_id: str | None
    confirmation_criteria: str
    contradiction_criteria: str
    uncertainty: str
    development_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.development_id, "development_id")
        if self.development_type not in {
            "operating",
            "financial",
            "competitive",
            "capital_allocation",
            "governance",
            "risk",
        }:
            raise FinancialDataValidationError("unsupported development type")
        _statement(self.statement, "expected development")
        _statement(self.confirmation_criteria, "confirmation criteria")
        _statement(self.contradiction_criteria, "contradiction criteria")
        timestamp(self.earliest_observation, "earliest observation")
        timestamp(self.latest_observation, "latest observation")
        if self.latest_observation < self.earliest_observation:
            raise FinancialDataValidationError("development window is invalid")
        if self.uncertainty not in {"low", "moderate", "high", "unknown"}:
            raise FinancialDataValidationError("expected development must be uncertain")
        object.__setattr__(
            self,
            "assumption_ids",
            _ids(self.assumption_ids, "development assumptions", required=True),
        )
        if self.causal_chain_id is not None:
            identifier(self.causal_chain_id, "causal_chain_id")
        _identity(self, "development_identity")


@dataclass(frozen=True, slots=True)
class ThesisArgument:
    argument_id: str
    argument_type: ThesisArgumentType
    statement: str
    subject_identity: str
    direction: str
    materiality: str
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...] = ()
    related_conflict_ids: tuple[str, ...] = ()
    related_gap_ids: tuple[str, ...] = ()
    assumption_ids: tuple[str, ...] = ()
    causal_mechanism: str = ""
    expected_development_ids: tuple[str, ...] = ()
    timeframe: str = ""
    stale: bool = False
    completeness: str = "complete"
    interpretation: bool = True
    argument_digest: str = ""

    def __post_init__(self) -> None:
        identifier(self.argument_id, "argument_id")
        if self.argument_type.value not in ARGUMENT_TYPES:
            raise FinancialDataValidationError("unsupported argument type")
        _statement(self.statement, "argument statement")
        digest(self.subject_identity, "subject_identity")
        if self.direction not in {
            "supports_thesis",
            "supports_counter_thesis",
            "neutral",
            "unresolved",
        }:
            raise FinancialDataValidationError("unsupported argument direction")
        if self.materiality not in MATERIALITIES:
            raise FinancialDataValidationError("unsupported materiality")
        object.__setattr__(
            self,
            "supporting_claim_ids",
            _ids(self.supporting_claim_ids, "supporting claims", required=True),
        )
        for field in (
            "contradicting_claim_ids",
            "related_conflict_ids",
            "related_gap_ids",
            "assumption_ids",
            "expected_development_ids",
        ):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        if not self.causal_mechanism.strip() or not self.timeframe.strip():
            raise FinancialDataValidationError("argument mechanism and timeframe are required")
        if self.completeness not in {"complete", "partial", "unavailable"}:
            raise FinancialDataValidationError("unsupported argument completeness")
        _identity(self, "argument_digest")


@dataclass(frozen=True, slots=True)
class ThesisCatalyst:
    catalyst_id: str
    event_type: str
    description: str
    expected_start: datetime | None
    expected_end: datetime | None
    supporting_claim_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    expected_observable_impact: str
    status: str
    uncertainty: str
    source_provenance: str
    speculative: bool = False
    catalyst_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.catalyst_id, "catalyst_id")
        _statement(self.description, "catalyst description")
        _statement(self.expected_observable_impact, "catalyst impact")
        if self.event_type not in {
            "scheduled_filing",
            "earnings_release",
            "product_milestone",
            "capacity_expansion",
            "customer_event",
            "regulatory_event",
            "debt_maturity",
            "capital_allocation_event",
            "margin_inflection_indicator",
            "financial_threshold",
            "governance_event",
        }:
            raise FinancialDataValidationError("unsupported catalyst type")
        if (self.expected_start is None) != (self.expected_end is None):
            raise FinancialDataValidationError("fabricated or incomplete catalyst window")
        if self.expected_start is not None:
            timestamp(self.expected_start, "catalyst start")
            timestamp(self.expected_end, "catalyst end")
            if self.expected_end < self.expected_start:  # type: ignore[operator]
                raise FinancialDataValidationError("catalyst window is invalid")
        if not self.speculative and not self.supporting_claim_ids:
            raise FinancialDataValidationError("scheduled catalyst requires evidence")
        object.__setattr__(
            self, "supporting_claim_ids", _ids(self.supporting_claim_ids, "catalyst claims")
        )
        object.__setattr__(
            self, "dependency_ids", _ids(self.dependency_ids, "catalyst dependencies")
        )
        digest(self.source_provenance, "source_provenance")
        _identity(self, "catalyst_identity")


@dataclass(frozen=True, slots=True)
class ThesisMonitoringIndicator:
    indicator_id: str
    description: str
    source_type: str
    observation_contract: str
    review_frequency: str
    stale_after: str
    materiality: str
    related_pillar_ids: tuple[str, ...] = ()
    related_assumption_ids: tuple[str, ...] = ()
    related_invalidation_ids: tuple[str, ...] = ()
    indicator_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.indicator_id, "indicator_id")
        for value, name in (
            (self.description, "indicator description"),
            (self.observation_contract, "observation contract"),
            (self.review_frequency, "review frequency"),
            (self.stale_after, "stale threshold"),
        ):
            _statement(value, name)
        if self.materiality not in MATERIALITIES:
            raise FinancialDataValidationError("unsupported materiality")
        for field in ("related_pillar_ids", "related_assumption_ids", "related_invalidation_ids"):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        _identity(self, "indicator_identity")


@dataclass(frozen=True, slots=True)
class ThesisInvalidationCondition:
    condition_id: str
    related_pillar_ids: tuple[str, ...]
    observable_condition: str
    evidence_type: str
    operator: str | None
    threshold: str | None
    time_window: str
    required_source: str
    status: str = "unevaluated"
    triggered_at: datetime | None = None
    evaluation_identity: str | None = None
    condition_digest: str = ""

    def __post_init__(self) -> None:
        identifier(self.condition_id, "condition_id")
        object.__setattr__(
            self,
            "related_pillar_ids",
            _ids(self.related_pillar_ids, "invalidation pillars", required=True),
        )
        _statement(self.observable_condition, "observable condition")
        vague = {
            "the company gets worse",
            "management disappoints",
            "the stock underperforms",
            "the thesis no longer feels right",
        }
        if self.observable_condition.casefold().strip() in vague:
            raise FinancialDataValidationError("vague invalidation condition")
        if (self.operator is None) != (self.threshold is None):
            raise FinancialDataValidationError("numeric invalidation rule is incomplete")
        if self.operator is not None:
            if self.operator not in {"<", "<=", "==", "!=", ">=", ">"}:
                raise FinancialDataValidationError("unsupported comparison operator")
            object.__setattr__(
                self,
                "threshold",
                decimal_text(self.threshold, "invalidation threshold", nonnegative=False),
            )
        if not self.time_window.strip() or not self.required_source.strip():
            raise FinancialDataValidationError("invalidation evidence contract is required")
        if self.status not in {"unevaluated", "triggered", "clear", "unavailable"}:
            raise FinancialDataValidationError("unsupported invalidation status")
        if self.status == "triggered" and self.triggered_at is None:
            raise FinancialDataValidationError("triggered invalidation requires timestamp")
        if self.triggered_at is not None:
            timestamp(self.triggered_at, "triggered_at")
        _identity(self, "condition_digest")


@dataclass(frozen=True, slots=True)
class ThesisFalsificationTest:
    test_id: str
    hypothesis: str
    required_observations: tuple[str, ...]
    comparison_rule: str
    expected_result: str
    falsifying_result: str
    evaluation_window: str
    status: str = "unevaluated"
    unavailable_reason: str | None = None
    test_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.test_id, "test_id")
        for value, name in (
            (self.hypothesis, "hypothesis"),
            (self.comparison_rule, "comparison rule"),
            (self.expected_result, "expected result"),
            (self.falsifying_result, "falsifying result"),
        ):
            _statement(value, name)
        object.__setattr__(
            self,
            "required_observations",
            _ids(self.required_observations, "required observations", required=True),
        )
        if self.status not in {"unevaluated", "passed", "falsified", "unavailable"}:
            raise FinancialDataValidationError("unsupported falsification status")
        if self.status == "unavailable" and not self.unavailable_reason:
            raise FinancialDataValidationError("unavailable falsification requires reason")
        _identity(self, "test_identity")


@dataclass(frozen=True, slots=True)
class ThesisRisk:
    risk_id: str
    category: str
    statement: str
    mechanism: str
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    probability: str
    impact: str
    materiality: str
    risk_metric_ids: tuple[str, ...] = ()
    mitigation_claim_ids: tuple[str, ...] = ()
    monitoring_indicator_ids: tuple[str, ...] = ()
    invalidation_condition_ids: tuple[str, ...] = ()
    risk_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.risk_id, "risk_id")
        _statement(self.statement, "risk statement")
        _statement(self.mechanism, "risk mechanism")
        object.__setattr__(
            self,
            "supporting_claim_ids",
            _ids(self.supporting_claim_ids, "risk claims", required=True),
        )
        for field in (
            "contradicting_claim_ids",
            "risk_metric_ids",
            "mitigation_claim_ids",
            "monitoring_indicator_ids",
            "invalidation_condition_ids",
        ):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        if self.probability not in {"remote", "possible", "likely", "unknown"}:
            raise FinancialDataValidationError("opaque risk score is forbidden")
        if (
            self.impact not in {"low", "moderate", "high", "critical"}
            or self.materiality not in MATERIALITIES
        ):
            raise FinancialDataValidationError("unsupported risk classification")
        _identity(self, "risk_identity")


@dataclass(frozen=True, slots=True)
class ThesisCausalChain:
    chain_id: str
    initiating_condition: str
    intermediate_mechanisms: tuple[str, ...]
    observable_effects: tuple[str, ...]
    financial_effects: tuple[str, ...]
    supporting_argument_ids: tuple[str, ...]
    contradicting_argument_ids: tuple[str, ...]
    assumption_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    timeframe: str
    failure_points: tuple[str, ...]
    monitoring_indicator_ids: tuple[str, ...]
    interpretation: bool = True
    chain_digest: str = ""

    def __post_init__(self) -> None:
        identifier(self.chain_id, "chain_id")
        _statement(self.initiating_condition, "initiating condition")
        if not self.interpretation:
            raise FinancialDataValidationError("causal chain must be marked interpretive")
        for field in (
            "intermediate_mechanisms",
            "observable_effects",
            "financial_effects",
            "supporting_argument_ids",
            "assumption_ids",
            "failure_points",
        ):
            values = getattr(self, field)
            if not values:
                raise FinancialDataValidationError(f"{field} is required")
            object.__setattr__(self, field, tuple(sorted(values)))
        for field in ("contradicting_argument_ids", "dependency_ids", "monitoring_indicator_ids"):
            object.__setattr__(self, field, tuple(sorted(getattr(self, field))))
        _identity(self, "chain_digest")


@dataclass(frozen=True, slots=True)
class ThesisValuationDependency:
    dependency_id: str
    dependency_type: str
    related_pillar_ids: tuple[str, ...]
    required_metric: str
    source_claim_ids: tuple[str, ...]
    assumption_ids: tuple[str, ...]
    sensitivity_direction: str
    invalidation_condition_ids: tuple[str, ...]
    dependency_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.dependency_id, "valuation dependency")
        if self.dependency_type not in {
            "continued_revenue_growth",
            "margin_expansion",
            "stable_share_count",
            "free_cash_flow_growth",
            "debt_reduction",
            "terminal_multiple_sensitivity",
            "interest_rate_sensitivity",
            "required_return_assumption",
            "market_price_dependency",
        }:
            raise FinancialDataValidationError("unsupported valuation dependency")
        for field in (
            "related_pillar_ids",
            "source_claim_ids",
            "assumption_ids",
            "invalidation_condition_ids",
        ):
            object.__setattr__(
                self,
                field,
                _ids(
                    getattr(self, field),
                    field,
                    required=field in {"related_pillar_ids", "source_claim_ids"},
                ),
            )
        if self.sensitivity_direction not in {"positive", "negative", "mixed", "unknown"}:
            raise FinancialDataValidationError("unsupported sensitivity direction")
        _identity(self, "dependency_identity")


@dataclass(frozen=True, slots=True)
class ThesisPortfolioRelevance:
    supplied: bool = False
    holding_status: str = "not_owned"
    portfolio_weight: str | None = None
    cost_basis: str | None = None
    realized_result: str | None = None
    unrealized_result: str | None = None
    concentration: str | None = None
    liquidity_exposure: str | None = None
    source_identity: str | None = None
    relevance_identity: str = ""

    def __post_init__(self) -> None:
        for field in (
            "portfolio_weight",
            "cost_basis",
            "realized_result",
            "unrealized_result",
            "concentration",
            "liquidity_exposure",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(
                    self,
                    field,
                    decimal_text(
                        value,
                        field,
                        nonnegative=field
                        in {
                            "portfolio_weight",
                            "cost_basis",
                            "concentration",
                            "liquidity_exposure",
                        },
                    ),
                )
        if self.source_identity is not None:
            digest(self.source_identity, "portfolio source identity")
        _identity(self, "relevance_identity")


@dataclass(frozen=True, slots=True)
class ThesisRiskRelevance:
    supplied: bool = False
    account_identity: str | None = None
    security_id: str | None = None
    risk_metric_ids: tuple[str, ...] = ()
    risk_limit_relevance: tuple[str, ...] = ()
    stress_relevance: tuple[str, ...] = ()
    proposed_trade_comparison_identity: str | None = None
    source_identity: str | None = None
    relevance_identity: str = ""

    def __post_init__(self) -> None:
        for field in ("risk_metric_ids", "risk_limit_relevance", "stress_relevance"):
            object.__setattr__(self, field, tuple(sorted(getattr(self, field))))
        for field in ("proposed_trade_comparison_identity", "source_identity"):
            value = getattr(self, field)
            if value is not None:
                digest(value, field)
        _identity(self, "relevance_identity")


@dataclass(frozen=True, slots=True)
class ThesisConflict:
    conflict_id: str
    dossier_conflict_identity: str
    impact: str
    related_argument_ids: tuple[str, ...] = ()
    related_pillar_ids: tuple[str, ...] = ()
    resolved: bool = False
    conflict_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.conflict_id, "conflict_id")
        digest(self.dossier_conflict_identity, "dossier conflict identity")
        if self.impact not in {
            "blocks_argument",
            "weakens_argument",
            "blocks_pillar",
            "weakens_pillar",
            "blocks_readiness",
            "monitoring_required",
            "informational",
        }:
            raise FinancialDataValidationError("unsupported conflict impact")
        for field in ("related_argument_ids", "related_pillar_ids"):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        _identity(self, "conflict_identity")


@dataclass(frozen=True, slots=True)
class ThesisEvidenceGap:
    gap_id: str
    dossier_gap_identity: str
    impact: str
    related_argument_ids: tuple[str, ...] = ()
    related_pillar_ids: tuple[str, ...] = ()
    stale: bool = False
    truncated: bool = False
    resolved: bool = False
    gap_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.gap_id, "gap_id")
        digest(self.dossier_gap_identity, "dossier gap identity")
        if self.impact not in {
            "blocks_argument",
            "weakens_argument",
            "blocks_pillar",
            "weakens_pillar",
            "blocks_readiness",
            "monitoring_required",
            "informational",
        }:
            raise FinancialDataValidationError("unsupported gap impact")
        for field in ("related_argument_ids", "related_pillar_ids"):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        _identity(self, "gap_identity")


@dataclass(frozen=True, slots=True)
class ThesisPillar:
    pillar_id: str
    title: str
    proposition: str
    argument_ids: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    assumption_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    catalyst_ids: tuple[str, ...]
    risk_ids: tuple[str, ...]
    invalidation_condition_ids: tuple[str, ...]
    monitoring_indicator_ids: tuple[str, ...]
    materiality: str
    completeness: ThesisCompletenessClassification
    confidence: ThesisConfidenceClassification
    pillar_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.pillar_id, "pillar_id")
        _statement(self.title, "pillar title", maximum=200)
        _statement(self.proposition, "pillar proposition")
        for field in ("argument_ids", "supporting_claim_ids", "invalidation_condition_ids"):
            object.__setattr__(self, field, _ids(getattr(self, field), field, required=True))
        for field in (
            "contradicting_claim_ids",
            "assumption_ids",
            "dependency_ids",
            "catalyst_ids",
            "risk_ids",
            "monitoring_indicator_ids",
        ):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        if self.materiality not in MATERIALITIES:
            raise FinancialDataValidationError("unsupported materiality")
        _identity(self, "pillar_identity")


@dataclass(frozen=True, slots=True)
class CounterThesisPillar(ThesisPillar):
    alternative_explanation: str = ""
    failure_mechanism: str = ""

    def __post_init__(self) -> None:
        super(CounterThesisPillar, self).__post_init__()
        _statement(self.alternative_explanation, "counter-thesis alternative explanation")
        _statement(self.failure_mechanism, "counter-thesis failure mechanism")
        if self.proposition.casefold().startswith(("not ", "it is not ")):
            raise FinancialDataValidationError("counter-thesis cannot be a mere negation")


@dataclass(frozen=True, slots=True)
class InvestmentThesis:
    hypothesis: str
    pillar_ids: tuple[str, ...]
    conclusion: str
    thesis_identity: str = ""

    def __post_init__(self) -> None:
        _statement(self.hypothesis, "investment hypothesis")
        _statement(self.conclusion, "thesis conclusion")
        object.__setattr__(
            self, "pillar_ids", _ids(self.pillar_ids, "thesis pillars", required=True)
        )
        _identity(self, "thesis_identity")


@dataclass(frozen=True, slots=True)
class InvestmentCounterThesis:
    hypothesis: str
    pillar_ids: tuple[str, ...]
    conclusion: str
    counter_thesis_identity: str = ""

    def __post_init__(self) -> None:
        _statement(self.hypothesis, "counter-thesis hypothesis")
        _statement(self.conclusion, "counter-thesis conclusion")
        object.__setattr__(
            self, "pillar_ids", _ids(self.pillar_ids, "counter-thesis pillars", required=True)
        )
        _identity(self, "counter_thesis_identity")


@dataclass(frozen=True, slots=True)
class InvestmentThesisProvenance:
    dossier_identity: str
    policy_identity: str
    claim_identities: tuple[str, ...]
    input_identities: tuple[str, ...]
    constructed_at: datetime
    engine_version: str = "sigil-thesis-v1"
    provenance_identity: str = ""

    def __post_init__(self) -> None:
        digest(self.dossier_identity, "dossier_identity")
        digest(self.policy_identity, "policy_identity")
        object.__setattr__(
            self, "claim_identities", _ids(self.claim_identities, "provenance claims")
        )
        object.__setattr__(
            self, "input_identities", _ids(self.input_identities, "provenance inputs")
        )
        timestamp(self.constructed_at, "constructed_at")
        _identity(self, "provenance_identity")


@dataclass(frozen=True, slots=True)
class InvestmentThesisInput:
    dossier_identity: str
    issuer_id: str
    security_id: str
    selected_claim_ids: tuple[str, ...]
    selected_conflict_ids: tuple[str, ...] = ()
    selected_gap_ids: tuple[str, ...] = ()
    risk_context_identity: str | None = None
    risk_account_identity: str | None = None
    portfolio_context_identity: str | None = None
    framing: str = "evidence-backed investment hypothesis"
    input_identity: str = ""

    def __post_init__(self) -> None:
        digest(self.dossier_identity, "dossier_identity")
        identifier(self.issuer_id, "issuer_id")
        identifier(self.security_id, "security_id")
        object.__setattr__(
            self,
            "selected_claim_ids",
            _ids(self.selected_claim_ids, "selected claims", required=True),
        )
        object.__setattr__(
            self, "selected_conflict_ids", _ids(self.selected_conflict_ids, "selected conflicts")
        )
        object.__setattr__(self, "selected_gap_ids", _ids(self.selected_gap_ids, "selected gaps"))
        _statement(self.framing, "thesis framing")
        if any(
            marker in self.framing.casefold()
            for marker in ("api_key", "access_token", "authorization", "private_key")
        ):
            raise FinancialDataValidationError("secret-bearing thesis input is forbidden")
        for field in ("risk_context_identity", "portfolio_context_identity"):
            value = getattr(self, field)
            if value is not None:
                digest(value, field)
        reject_secret_bearing(asdict(self))
        _identity(self, "input_identity")


@dataclass(frozen=True, slots=True)
class InvestmentThesisPackage:
    package_version: str
    policy_identity: str
    dossier_identity: str
    issuer_id: str
    security_id: str
    constructed_at: datetime
    thesis_horizon: str
    investment_thesis: InvestmentThesis
    counter_thesis: InvestmentCounterThesis
    arguments: tuple[ThesisArgument, ...]
    thesis_pillars: tuple[ThesisPillar, ...]
    counter_thesis_pillars: tuple[CounterThesisPillar, ...]
    assumptions: tuple[ThesisAssumption, ...]
    dependencies: tuple[ThesisDependency, ...]
    causal_chains: tuple[ThesisCausalChain, ...]
    catalysts: tuple[ThesisCatalyst, ...]
    risks: tuple[ThesisRisk, ...]
    invalidation_conditions: tuple[ThesisInvalidationCondition, ...]
    falsification_tests: tuple[ThesisFalsificationTest, ...]
    monitoring_indicators: tuple[ThesisMonitoringIndicator, ...]
    expected_developments: tuple[ThesisExpectedDevelopment, ...]
    valuation_dependencies: tuple[ThesisValuationDependency, ...]
    portfolio_relevance: ThesisPortfolioRelevance
    risk_relevance: ThesisRiskRelevance
    conflicts: tuple[ThesisConflict, ...]
    evidence_gaps: tuple[ThesisEvidenceGap, ...]
    confidence: ThesisConfidenceClassification
    completeness: ThesisCompletenessClassification
    readiness: ThesisReadinessClassification
    unavailable_reasons: tuple[InvestmentThesisUnavailableReason, ...]
    provenance: InvestmentThesisProvenance
    readiness_blockers: tuple[str, ...] = ()
    confidence_components: tuple[tuple[str, str], ...] = ()
    package_identity: str = ""

    def __post_init__(self) -> None:
        digest(self.policy_identity, "policy_identity")
        digest(self.dossier_identity, "dossier_identity")
        identifier(self.issuer_id, "issuer_id")
        identifier(self.security_id, "security_id")
        timestamp(self.constructed_at, "constructed_at")
        fields = (
            ("arguments", "argument_digest"),
            ("thesis_pillars", "pillar_identity"),
            ("counter_thesis_pillars", "pillar_identity"),
            ("assumptions", "assumption_identity"),
            ("dependencies", "dependency_identity"),
            ("causal_chains", "chain_digest"),
            ("catalysts", "catalyst_identity"),
            ("risks", "risk_identity"),
            ("invalidation_conditions", "condition_digest"),
            ("falsification_tests", "test_identity"),
            ("monitoring_indicators", "indicator_identity"),
            ("expected_developments", "development_identity"),
            ("valuation_dependencies", "dependency_identity"),
            ("conflicts", "conflict_identity"),
            ("evidence_gaps", "gap_identity"),
        )
        for field, identity_field in fields:
            object.__setattr__(self, field, _objects(getattr(self, field), identity_field, field))
        if (
            self.dossier_identity != self.provenance.dossier_identity
            or self.policy_identity != self.provenance.policy_identity
        ):
            raise FinancialDataValidationError("thesis provenance mismatch")
        object.__setattr__(
            self,
            "unavailable_reasons",
            tuple(sorted(set(self.unavailable_reasons), key=lambda item: item.value)),
        )
        object.__setattr__(self, "readiness_blockers", tuple(sorted(set(self.readiness_blockers))))
        object.__setattr__(self, "confidence_components", tuple(sorted(self.confidence_components)))
        _identity(self, "package_identity")


@dataclass(frozen=True, slots=True)
class InvestmentThesisComparison:
    before_identity: str
    after_identity: str
    changes: tuple[tuple[str, tuple[str, ...]], ...]
    confidence_change: tuple[str, str] | None
    completeness_change: tuple[str, str] | None
    readiness_change: tuple[str, str] | None
    dossier_identity_change: tuple[str, str] | None
    comparison_identity: str = ""

    def __post_init__(self) -> None:
        digest(self.before_identity, "before_identity")
        digest(self.after_identity, "after_identity")
        object.__setattr__(self, "changes", tuple(sorted(self.changes)))
        _identity(self, "comparison_identity")
