"""Immutable contracts for governed, evidence-backed research dossiers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
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

SUPPORTED_CURRENCIES = frozenset({"USD"})
SUPPORTED_INSTRUMENTS = frozenset({"EQUITY", "ETF"})
SUPPORTED_SECURITY_TYPES = frozenset({"COMMON_STOCK", "ETF"})
SUPPORTED_SOURCE_TYPES = frozenset(
    {
        "financial_document",
        "sec_filing",
        "extracted_evidence",
        "provider_fact",
        "market_data",
        "sentiment",
        "portfolio_snapshot",
        "accounting_report",
        "risk_report",
        "verified_classification",
        "verified_observation",
        "dossier_policy",
    }
)
SUPPORTED_SECTIONS = frozenset(
    {
        "identity",
        "business_profile",
        "financial_history",
        "management",
        "governance",
        "filings",
        "risk_factors",
        "valuation",
        "sentiment",
        "portfolio_relevance",
        "risk_relevance",
        "competitive_positioning",
        "industry_context",
    }
)
PROHIBITED_RECOMMENDATIONS = (
    "buy",
    "sell",
    "hold",
    "target price",
    "price target",
    "guaranteed",
    "allocate",
    "trade instruction",
)


def _identity(instance: object, field_name: str) -> None:
    material = {key: value for key, value in asdict(instance).items() if key != field_name}
    computed = canonical_digest(material)
    supplied = getattr(instance, field_name)
    if supplied and supplied != computed:
        raise FinancialDataValidationError(f"{field_name} mismatch")
    object.__setattr__(instance, field_name, computed)


def _ordered_unique(values: tuple[object, ...], key: str, name: str) -> tuple[object, ...]:
    ordered = tuple(sorted(values, key=lambda item: getattr(item, key)))
    identities = [getattr(item, key) for item in ordered]
    if len(set(identities)) != len(identities):
        raise FinancialDataValidationError(f"duplicate {name}")
    return ordered


class ResearchFreshnessStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class ResearchCompletenessStatus(StrEnum):
    COMPLETE = "complete"
    SUBSTANTIALLY_COMPLETE = "substantially_complete"
    PARTIAL = "partial"
    MATERIALLY_INCOMPLETE = "materially_incomplete"
    UNAVAILABLE = "unavailable"


class ResearchDossierUnavailableReason(StrEnum):
    UNRESOLVED_IDENTITY = "unresolved_identity"
    REQUIRED_EVIDENCE_MISSING = "required_evidence_missing"
    INVALID_PROVENANCE = "invalid_provenance"
    INSUFFICIENT_FINANCIAL_HISTORY = "insufficient_financial_history"
    MATERIAL_CONFLICT = "material_conflict"
    STALE_REQUIRED_EVIDENCE = "stale_required_evidence"
    TRUNCATED_REQUIRED_EVIDENCE = "truncated_required_evidence"
    INVALID_DENOMINATOR = "invalid_denominator"
    INCOMPATIBLE_PERIODS = "incompatible_periods"
    INCOMPATIBLE_UNITS = "incompatible_units"


@dataclass(frozen=True, slots=True)
class ResearchEntityIdentity:
    issuer_id: str
    legal_name: str
    normalized_name: str
    cik: str | None = None
    provider_identifiers: tuple[tuple[str, str], ...] = ()
    resolved: bool = True
    identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.issuer_id, "issuer_id")
        if not self.legal_name.strip() or not self.normalized_name.strip():
            raise FinancialDataValidationError("issuer name is required")
        if not self.resolved:
            raise FinancialDataValidationError("ambiguous issuer identity")
        if self.cik is not None and (not self.cik.isdigit() or len(self.cik) != 10):
            raise FinancialDataValidationError("CIK is invalid")
        providers = tuple(sorted(self.provider_identifiers))
        if len({name for name, _ in providers}) != len(providers):
            raise FinancialDataValidationError("conflicting provider identifiers")
        for name, value in providers:
            identifier(name, "provider")
            identifier(value, "provider identifier")
        object.__setattr__(self, "provider_identifiers", providers)
        reject_secret_bearing(asdict(self))
        _identity(self, "identity")


@dataclass(frozen=True, slots=True)
class ResearchSecurityIdentity:
    security_id: str
    issuer_id: str
    ticker: str
    instrument_type: str
    security_type: str
    currency: str
    exchange: str | None = None
    cik: str | None = None
    provider_identifiers: tuple[tuple[str, str], ...] = ()
    identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.security_id, "security_id")
        identifier(self.issuer_id, "issuer_id")
        object.__setattr__(self, "ticker", symbol(self.ticker))
        if self.instrument_type not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("unsupported instrument")
        if self.security_type not in SUPPORTED_SECURITY_TYPES:
            raise FinancialDataValidationError("unsupported security type")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")
        if self.exchange is not None:
            identifier(self.exchange, "exchange")
        if self.cik is not None and (not self.cik.isdigit() or len(self.cik) != 10):
            raise FinancialDataValidationError("CIK is invalid")
        object.__setattr__(self, "provider_identifiers", tuple(sorted(self.provider_identifiers)))
        reject_secret_bearing(asdict(self))
        _identity(self, "identity")


def validate_identity_binding(
    entity: ResearchEntityIdentity, security: ResearchSecurityIdentity | None
) -> None:
    if security is None:
        return
    if security.issuer_id != entity.issuer_id:
        raise FinancialDataValidationError("issuer/security mismatch")
    if entity.cik and security.cik and entity.cik != security.cik:
        raise FinancialDataValidationError("CIK mismatch")


@dataclass(frozen=True, slots=True)
class ResearchEvidenceReference:
    evidence_id: str
    source_identity: str
    source_type: str
    source_record_id: str
    source_digest: str
    source_timestamp: datetime
    acquired_at: datetime
    entity_id: str
    security_id: str | None = None
    document_identity: str | None = None
    filing_identity: str | None = None
    locator: str | None = None
    fact_digest: str = ""
    extraction_method: str = "normalized"
    verification_status: str = "verified"
    completeness: str = "complete"
    truncated: bool = False
    supersession_status: str = "current"
    excerpt: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.evidence_id, "evidence_id"),
            (self.source_identity, "source_identity"),
            (self.source_record_id, "source_record_id"),
            (self.entity_id, "entity_id"),
        ):
            identifier(value, name)
        if self.source_type not in SUPPORTED_SOURCE_TYPES:
            raise FinancialDataValidationError("unsupported source type")
        digest(self.source_digest, "source_digest")
        digest(self.fact_digest, "fact_digest")
        timestamp(self.source_timestamp, "source_timestamp")
        timestamp(self.acquired_at, "acquired_at")
        if self.source_timestamp > self.acquired_at:
            raise FinancialDataValidationError("source timestamp is after acquisition")
        if self.security_id is not None:
            identifier(self.security_id, "security_id")
        if (
            self.source_type in {"financial_document", "sec_filing", "extracted_evidence"}
            and not self.locator
        ):
            raise FinancialDataValidationError("evidence locator is required")
        if self.verification_status != "verified":
            raise FinancialDataValidationError("evidence is not verified")
        if self.truncated and self.completeness == "complete":
            raise FinancialDataValidationError("truncated evidence cannot be complete")
        if self.excerpt is not None and len(self.excerpt) > 500:
            raise FinancialDataValidationError("evidence excerpt exceeds bound")
        reject_secret_bearing(asdict(self))


@dataclass(frozen=True, slots=True)
class ResearchEvidenceClaim:
    claim_id: str
    claim_type: str
    subject: str
    predicate: str
    normalized_value: str
    classification: str
    evidence_references: tuple[ResearchEvidenceReference, ...] = ()
    source_claim_ids: tuple[str, ...] = ()
    units: str | None = None
    currency: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    formula: str | None = None
    confidence: str = "supported"
    contradiction_status: str = "none"
    freshness: ResearchFreshnessStatus = ResearchFreshnessStatus.CURRENT
    materiality: str = "informational"
    explanation_code: str = "direct_source"
    claim_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.claim_id, "claim_id")
        identifier(self.claim_type, "claim_type")
        identifier(self.subject, "claim subject")
        identifier(self.predicate, "claim predicate")
        if self.classification not in {"source_fact", "derived"}:
            raise FinancialDataValidationError("claim classification is unsupported")
        if self.classification == "source_fact" and not self.evidence_references:
            raise FinancialDataValidationError("source-fact claim requires evidence")
        if self.classification == "derived" and (not self.source_claim_ids or not self.formula):
            raise FinancialDataValidationError("derived claim requires source claims and formula")
        evidence = _ordered_unique(
            self.evidence_references, "evidence_id", "claim evidence identities"
        )
        object.__setattr__(self, "evidence_references", evidence)
        sources = tuple(sorted(self.source_claim_ids))
        if len(set(sources)) != len(sources):
            raise FinancialDataValidationError("duplicate source claim identities")
        object.__setattr__(self, "source_claim_ids", sources)
        if self.currency is not None and self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")
        if (self.period_start is None) != (self.period_end is None):
            raise FinancialDataValidationError("claim period is incomplete")
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise FinancialDataValidationError("claim period is invalid")
        reject_secret_bearing(asdict(self))
        _identity(self, "claim_identity")


@dataclass(frozen=True, slots=True)
class ResearchEvidenceConflict:
    conflict_id: str
    affected_field: str
    category: str
    evidence_references: tuple[ResearchEvidenceReference, ...]
    competing_values: tuple[str, ...]
    materiality: str
    period: str | None = None
    resolution_status: str = "unresolved"
    selected_value: str | None = None
    resolution_reason: str | None = None
    resolver_identity: str | None = None
    conflict_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.conflict_id, "conflict_id")
        if len(self.evidence_references) < 2 or len(set(self.competing_values)) < 2:
            raise FinancialDataValidationError("conflict requires competing evidence and values")
        object.__setattr__(
            self,
            "evidence_references",
            _ordered_unique(self.evidence_references, "evidence_id", "conflict evidence"),
        )
        object.__setattr__(self, "competing_values", tuple(sorted(self.competing_values)))
        if self.resolution_status == "resolved":
            if self.selected_value not in self.competing_values or not self.resolution_reason:
                raise FinancialDataValidationError("resolved conflict requires governed selection")
        elif self.selected_value is not None:
            raise FinancialDataValidationError("unresolved conflict cannot select a value")
        _identity(self, "conflict_identity")


@dataclass(frozen=True, slots=True)
class ResearchEvidenceGap:
    gap_id: str
    section: str
    reason: str
    materiality: str
    required_evidence_type: str | None = None
    related_claim_ids: tuple[str, ...] = ()
    stale: bool = False
    truncated: bool = False
    resolved: bool = False
    gap_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.gap_id, "gap_id")
        if self.section not in SUPPORTED_SECTIONS:
            raise FinancialDataValidationError("unsupported gap section")
        object.__setattr__(self, "related_claim_ids", tuple(sorted(self.related_claim_ids)))
        _identity(self, "gap_identity")


@dataclass(frozen=True, slots=True)
class FinancialPeriodObservation:
    observation_id: str
    entity_id: str
    metric: str
    period_start: date
    period_end: date
    fiscal_year: int
    value: str
    units: str
    evidence_reference: ResearchEvidenceReference
    period_kind: str = "annual"
    fiscal_quarter: int | None = None
    balance_type: str = "duration"
    currency: str | None = "USD"
    filing_identity: str | None = None
    amendment_status: str = "original"
    completeness: str = "complete"
    confidence: str = "supported"
    observation_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.observation_id, "observation_id")
        identifier(self.entity_id, "entity_id")
        identifier(self.metric, "metric")
        if self.period_end < self.period_start:
            raise FinancialDataValidationError("financial period is invalid")
        if self.period_kind not in {"annual", "quarterly"}:
            raise FinancialDataValidationError("financial period kind is unsupported")
        if self.period_kind == "quarterly" and self.fiscal_quarter not in {1, 2, 3, 4}:
            raise FinancialDataValidationError("quarterly observation requires fiscal quarter")
        if self.period_kind == "annual" and self.fiscal_quarter is not None:
            raise FinancialDataValidationError("annual observation cannot have fiscal quarter")
        if self.balance_type not in {"instant", "duration"}:
            raise FinancialDataValidationError("balance type is unsupported")
        object.__setattr__(
            self, "value", decimal_text(self.value, "financial value", nonnegative=False)
        )
        if self.currency is not None and self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")
        if self.entity_id != self.evidence_reference.entity_id:
            raise FinancialDataValidationError("cross-issuer evidence injection")
        _identity(self, "observation_identity")


@dataclass(frozen=True, slots=True)
class FinancialHistory:
    observations: tuple[FinancialPeriodObservation, ...]
    history_identity: str = ""

    def __post_init__(self) -> None:
        ordered = _ordered_unique(self.observations, "observation_identity", "financial fact")
        keys: set[tuple[object, ...]] = set()
        for item in ordered:
            key = (item.metric, item.period_start, item.period_end, item.period_kind)
            if key in keys:
                raise FinancialDataValidationError("duplicate financial fact")
            keys.add(key)
        object.__setattr__(self, "observations", ordered)
        _identity(self, "history_identity")


@dataclass(frozen=True, slots=True)
class FinancialDerivedValue:
    metric: str
    value: str | None
    source_observation_ids: tuple[str, ...]
    formula: str
    unavailable_reason: ResearchDossierUnavailableReason | None = None
    identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.metric, "derived metric")
        if (self.value is None) == (self.unavailable_reason is None):
            raise FinancialDataValidationError("derived value availability is inconsistent")
        if self.value is not None:
            object.__setattr__(
                self, "value", decimal_text(self.value, "derived value", nonnegative=False)
            )
        object.__setattr__(
            self, "source_observation_ids", tuple(sorted(self.source_observation_ids))
        )
        _identity(self, "identity")


@dataclass(frozen=True, slots=True)
class RevenueAnalysis:
    growth: tuple[FinancialDerivedValue, ...] = ()
    cagr: FinancialDerivedValue | None = None
    trend: str = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class MarginAnalysis:
    margins: tuple[FinancialDerivedValue, ...] = ()
    trend: str = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class CashFlowAnalysis:
    free_cash_flow: tuple[FinancialDerivedValue, ...] = ()
    conversion: tuple[FinancialDerivedValue, ...] = ()
    trend: str = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class BalanceSheetAnalysis:
    net_cash_or_debt: tuple[FinancialDerivedValue, ...] = ()
    leverage: tuple[FinancialDerivedValue, ...] = ()
    trend: str = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class ShareCountAnalysis:
    changes: tuple[FinancialDerivedValue, ...] = ()
    repurchase_offset: tuple[FinancialDerivedValue, ...] = ()


@dataclass(frozen=True, slots=True)
class CapitalAllocationAnalysis:
    observations: tuple[FinancialDerivedValue, ...] = ()


@dataclass(frozen=True, slots=True)
class OperatingSegmentProfile:
    segment_id: str
    name: str
    description: str
    claim_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.segment_id, "segment_id")
        if not self.claim_ids:
            raise FinancialDataValidationError("segment profile requires evidence claims")


@dataclass(frozen=True, slots=True)
class GeographicExposure:
    geography: str
    value: str | None
    units: str | None
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CustomerExposure:
    customer: str
    concentration: str | None
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupplierDependency:
    supplier: str
    dependency: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BusinessProfile:
    description: str
    claim_ids: tuple[str, ...]
    products_services: tuple[str, ...] = ()
    revenue_model: tuple[str, ...] = ()
    segments: tuple[OperatingSegmentProfile, ...] = ()
    geographic_exposure: tuple[GeographicExposure, ...] = ()
    customers: tuple[CustomerExposure, ...] = ()
    suppliers: tuple[SupplierDependency, ...] = ()
    distribution_channels: tuple[str, ...] = ()
    regulatory_dependencies: tuple[str, ...] = ()
    key_assets: tuple[str, ...] = ()
    intellectual_property: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.claim_ids:
            raise FinancialDataValidationError("business profile requires evidence claims")


@dataclass(frozen=True, slots=True)
class ManagementProfile:
    person_id: str
    name: str
    role: str
    claim_ids: tuple[str, ...]
    tenure_start: date | None = None


@dataclass(frozen=True, slots=True)
class GovernanceProfile:
    board_structure: str
    claim_ids: tuple[str, ...]
    auditor: str | None = None
    independence: str | None = None


@dataclass(frozen=True, slots=True)
class ValuationObservation:
    observation_id: str
    metric: str
    value: str
    as_of: datetime
    evidence_reference: ResearchEvidenceReference
    currency: str | None = None
    denominator_claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.observation_id, "valuation observation")
        object.__setattr__(
            self, "value", decimal_text(self.value, "valuation value", nonnegative=False)
        )
        timestamp(self.as_of, "valuation as_of")
        if self.currency is not None and self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("unsupported currency")


@dataclass(frozen=True, slots=True)
class ValuationContext:
    observations: tuple[ValuationObservation, ...]
    stale: bool = False
    unavailable_reasons: tuple[ResearchDossierUnavailableReason, ...] = ()


@dataclass(frozen=True, slots=True)
class EarningsObservation:
    period: str
    claim_ids: tuple[str, ...]
    actual: str | None = None
    estimate: str | None = None


@dataclass(frozen=True, slots=True)
class GuidanceObservation:
    period: str
    metric: str
    lower: str | None
    upper: str | None
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FilingObservation:
    filing_id: str
    filing_type: str
    filed_at: date
    reporting_period: date
    source_locator: str
    source_digest: str
    acquired_at: datetime
    amendment_status: str = "original"
    amends_filing_id: str | None = None
    completeness: str = "complete"
    extraction_status: str = "complete"

    def __post_init__(self) -> None:
        identifier(self.filing_id, "filing_id")
        digest(self.source_digest, "source_digest")
        timestamp(self.acquired_at, "acquired_at")
        if self.amendment_status == "amended" and not self.amends_filing_id:
            raise FinancialDataValidationError("amendment relationship is required")


@dataclass(frozen=True, slots=True)
class RiskFactorObservation:
    risk_id: str
    category: str
    title: str
    summary: str
    evidence_reference: ResearchEvidenceReference
    materiality: str
    freshness: ResearchFreshnessStatus
    recurrence: int | None = None
    change_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class LitigationRegulatoryObservation:
    observation_id: str
    category: str
    summary: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompetitiveObservation:
    observation_id: str
    summary: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IndustryObservation:
    observation_id: str
    summary: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SentimentObservation:
    observation_id: str
    model_identity: str
    model_version: str
    input_digest: str
    observed_at: datetime
    label: str
    confidence: str
    evidence_reference: ResearchEvidenceReference
    stale: bool = False

    def __post_init__(self) -> None:
        digest(self.input_digest, "input_digest")
        timestamp(self.observed_at, "sentiment timestamp")
        object.__setattr__(
            self, "confidence", decimal_text(self.confidence, "sentiment confidence")
        )


@dataclass(frozen=True, slots=True)
class PortfolioRelevance:
    supplied: bool = False
    holding_status: str = "not_supplied"
    portfolio_weight: str | None = None
    cost_basis: str | None = None
    unrealized_gain_loss: str | None = None
    realized_gain_loss: str | None = None
    source_identity: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "portfolio_weight",
            "cost_basis",
            "unrealized_gain_loss",
            "realized_gain_loss",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self, name, decimal_text(value, name, nonnegative=name == "portfolio_weight")
                )


@dataclass(frozen=True, slots=True)
class RiskRelevance:
    supplied: bool = False
    concentration: str | None = None
    liquidity: str | None = None
    limit_relevance: tuple[str, ...] = ()
    stress_relevance: tuple[str, ...] = ()
    source_identity: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    question_id: str
    section: str
    text: str
    reason_code: str
    materiality: str
    required_evidence_type: str
    created_at: datetime
    related_claim_ids: tuple[str, ...] = ()
    related_gap_ids: tuple[str, ...] = ()
    related_conflict_ids: tuple[str, ...] = ()
    status: str = "open"
    question_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.question_id, "question_id")
        if self.section not in SUPPORTED_SECTIONS:
            raise FinancialDataValidationError("unsupported question section")
        timestamp(self.created_at, "question created_at")
        _identity(self, "question_identity")


@dataclass(frozen=True, slots=True)
class ResearchConclusion:
    conclusion_id: str
    section: str
    conclusion_type: str
    statement: str
    supporting_claim_ids: tuple[str, ...]
    generated_at: datetime
    contradicting_claim_ids: tuple[str, ...] = ()
    related_gap_ids: tuple[str, ...] = ()
    materiality: str = "informational"
    confidence: str = "supported"
    rule_identity: str | None = None
    conclusion_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.conclusion_id, "conclusion_id")
        if self.section not in SUPPORTED_SECTIONS:
            raise FinancialDataValidationError("unsupported conclusion section")
        if not self.supporting_claim_ids:
            raise FinancialDataValidationError("conclusion requires supporting claims")
        lowered = self.statement.casefold()
        if any(term in lowered for term in PROHIBITED_RECOMMENDATIONS):
            raise FinancialDataValidationError("prohibited recommendation language")
        if len(self.statement) > 1_000:
            raise FinancialDataValidationError("conclusion statement exceeds bound")
        timestamp(self.generated_at, "conclusion generated_at")
        object.__setattr__(
            self, "supporting_claim_ids", tuple(sorted(self.supporting_claim_ids))
        )
        object.__setattr__(
            self, "contradicting_claim_ids", tuple(sorted(self.contradicting_claim_ids))
        )
        object.__setattr__(self, "related_gap_ids", tuple(sorted(self.related_gap_ids)))
        _identity(self, "conclusion_identity")


@dataclass(frozen=True, slots=True)
class ResearchDossierProvenance:
    policy_identity: str
    evidence_identities: tuple[str, ...]
    claim_identities: tuple[str, ...]
    constructed_at: datetime
    engine_version: str = "sigil-dossier-v1"
    provenance_identity: str = ""

    def __post_init__(self) -> None:
        digest(self.policy_identity, "policy_identity")
        timestamp(self.constructed_at, "constructed_at")
        object.__setattr__(self, "evidence_identities", tuple(sorted(self.evidence_identities)))
        object.__setattr__(self, "claim_identities", tuple(sorted(self.claim_identities)))
        _identity(self, "provenance_identity")


@dataclass(frozen=True, slots=True)
class ResearchDossier:
    entity: ResearchEntityIdentity
    security: ResearchSecurityIdentity | None
    policy_identity: str
    constructed_at: datetime
    claims: tuple[ResearchEvidenceClaim, ...]
    conflicts: tuple[ResearchEvidenceConflict, ...]
    gaps: tuple[ResearchEvidenceGap, ...]
    questions: tuple[ResearchQuestion, ...]
    conclusions: tuple[ResearchConclusion, ...]
    completeness: ResearchCompletenessStatus
    high_confidence_eligible: bool
    provenance: ResearchDossierProvenance
    dossier_version: str = "sigil-dossier-v1"
    business_profile: BusinessProfile | None = None
    financial_history: FinancialHistory | None = None
    revenue_analysis: RevenueAnalysis | None = None
    margin_analysis: MarginAnalysis | None = None
    cash_flow_analysis: CashFlowAnalysis | None = None
    balance_sheet_analysis: BalanceSheetAnalysis | None = None
    share_count_analysis: ShareCountAnalysis | None = None
    capital_allocation_analysis: CapitalAllocationAnalysis | None = None
    management: tuple[ManagementProfile, ...] = ()
    governance: GovernanceProfile | None = None
    filings: tuple[FilingObservation, ...] = ()
    risk_factors: tuple[RiskFactorObservation, ...] = ()
    litigation_regulatory: tuple[LitigationRegulatoryObservation, ...] = ()
    competitive: tuple[CompetitiveObservation, ...] = ()
    industry: tuple[IndustryObservation, ...] = ()
    sentiment: tuple[SentimentObservation, ...] = ()
    valuation: ValuationContext | None = None
    portfolio_relevance: PortfolioRelevance | None = None
    risk_relevance: RiskRelevance | None = None
    unavailable_reasons: tuple[ResearchDossierUnavailableReason, ...] = ()
    dossier_identity: str = ""

    def __post_init__(self) -> None:
        validate_identity_binding(self.entity, self.security)
        digest(self.policy_identity, "policy_identity")
        timestamp(self.constructed_at, "constructed_at")
        for field_name, key, label in (
            ("claims", "claim_identity", "claims"),
            ("conflicts", "conflict_identity", "conflicts"),
            ("gaps", "gap_identity", "gaps"),
            ("questions", "question_identity", "questions"),
            ("conclusions", "conclusion_identity", "conclusions"),
        ):
            object.__setattr__(
                self, field_name, _ordered_unique(getattr(self, field_name), key, label)
            )
        evidence_ids = {
            reference.evidence_id
            for claim in self.claims
            for reference in claim.evidence_references
        }
        if tuple(sorted(evidence_ids)) != self.provenance.evidence_identities:
            raise FinancialDataValidationError("dossier provenance evidence mismatch")
        if tuple(claim.claim_identity for claim in self.claims) != self.provenance.claim_identities:
            raise FinancialDataValidationError("dossier provenance claim mismatch")
        if self.policy_identity != self.provenance.policy_identity:
            raise FinancialDataValidationError("dossier provenance policy mismatch")
        _identity(self, "dossier_identity")


# Explicit aliases for required section responsibilities that share the normalized contracts.
ResearchSecurityIdentity.__doc__ = "Exact supported security binding for one dossier."
