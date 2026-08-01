"""Immutable model and provider vocabulary for governed Sigil AI routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, IntEnum

AI_CONTRACT_VERSION = 1
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SENSITIVE_METADATA_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "password",
    "secret",
    "token",
)


def validate_identifier(value: str, field: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a stable lowercase identifier")
    return value


def validate_safe_metadata(metadata: tuple[tuple[str, str], ...], field: str) -> None:
    if tuple(sorted(metadata)) != metadata:
        raise ValueError(f"{field} must be deterministically sorted")
    for key, value in metadata:
        normalized = f"{key} {value}".lower()
        if any(marker in normalized for marker in _SENSITIVE_METADATA_MARKERS):
            raise ValueError(f"{field} cannot contain credential-bearing data")


class Capability(str, Enum):
    REASONING = "reasoning.v1"
    STRUCTURED_GENERATION = "structured_generation.v1"
    CODING = "coding.v1"
    SUMMARIZATION = "summarization.v1"
    FINANCIAL_SENTIMENT = "financial_sentiment.v1"
    EMBEDDINGS = "embeddings.v1"
    SEMANTIC_RETRIEVAL = "semantic_retrieval.v1"
    RETRIEVAL_RERANKING = "retrieval_reranking.v1"
    TIME_SERIES_FORECASTING = "time_series_forecasting.v1"
    MULTIMODAL_ANALYSIS = "multimodal_analysis.v1"
    ORCHESTRATION = "orchestration.v1"


class ExecutionLocation(str, Enum):
    LOCAL = "local"
    FLEET = "fleet"
    EXTERNAL = "external"


class InputType(str, Enum):
    TEXT = "text"
    STRUCTURED_JSON = "structured_json"
    IMAGE = "image"
    TIME_SERIES = "time_series"


class CostClass(IntEnum):
    FREE = 0
    LOW = 1
    STANDARD = 2
    HIGH = 3


class TrustTier(IntEnum):
    UNTRUSTED = 0
    RESTRICTED = 1
    TRUSTED = 2
    PRIVILEGED = 3


class PrivacyTier(IntEnum):
    EXTERNAL_APPROVED = 0
    GOVERNED_REMOTE = 1
    LOCAL_ONLY = 2


class ProviderHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class Responsibility(str, Enum):
    ANALYSIS = "analysis"
    EXPLANATION = "explanation"
    RESEARCH = "research"
    SENTIMENT = "sentiment"
    RETRIEVAL = "retrieval"
    FORECASTING = "forecasting"
    CODE_ASSISTANCE = "code_assistance"
    ORCHESTRATION_PLANNING = "orchestration_planning"
    RESEARCH_ANALYSIS = "research_analysis"
    PROPOSAL_SUPPORT = "proposal_support"
    EVIDENCE_SUMMARIZATION = "evidence_summarization"
    RISK_ANALYSIS = "risk_analysis"
    MARKET_CONTEXT = "market_context"
    FINANCIAL_SENTIMENT_ANALYSIS = "financial_sentiment_analysis"
    NEWS_SENTIMENT = "news_sentiment"
    EARNINGS_SENTIMENT = "earnings_sentiment"
    RESEARCH_RETRIEVAL = "research_retrieval"
    EVIDENCE_RETRIEVAL = "evidence_retrieval"
    PROPOSAL_CONTEXT = "proposal_context"
    AUDIT_CONTEXT = "audit_context"
    MARKET_FORECASTING = "market_forecasting"
    SCENARIO_ANALYSIS = "scenario_analysis"
    ORCHESTRATION_SUPPORT = "orchestration_support"
    AUTHORIZE_CAPITAL = "authorize_capital"
    CHANGE_POLICY = "change_policy"
    APPROVE_PROPOSAL = "approve_proposal"
    SUBMIT_BROKER_ORDER = "submit_broker_order"
    BYPASS_OPERATOR_CONFIRMATION = "bypass_operator_confirmation"
    FABRICATE_MISSING_EVIDENCE = "fabricate_missing_evidence"
    CAPITAL_AUTHORIZATION = "capital_authorization"
    PROPOSAL_APPROVAL = "proposal_approval"
    POLICY_CHANGE = "policy_change"
    BROKER_SUBMISSION = "broker_submission"
    ORDER_EXECUTION = "order_execution"
    PORTFOLIO_MUTATION = "portfolio_mutation"
    CREDENTIAL_ACCESS = "credential_access"
    UNRESTRICTED_SHELL_EXECUTION = "unrestricted_shell_execution"
    SOURCE_DELETION_WITHOUT_GOVERNED_OPERATOR_ACTION = (
        "source_deletion_without_governed_operator_action"
    )
    AUTOMATIC_STRATEGY_PROMOTION = "automatic_strategy_promotion"
    AUTOMATIC_FORECAST_DRIVEN_TRADING = "automatic_forecast_driven_trading"


PROHIBITED_RESPONSIBILITIES = frozenset(
    {
        Responsibility.AUTHORIZE_CAPITAL,
        Responsibility.CHANGE_POLICY,
        Responsibility.APPROVE_PROPOSAL,
        Responsibility.SUBMIT_BROKER_ORDER,
        Responsibility.BYPASS_OPERATOR_CONFIRMATION,
        Responsibility.FABRICATE_MISSING_EVIDENCE,
        Responsibility.CAPITAL_AUTHORIZATION,
        Responsibility.PROPOSAL_APPROVAL,
        Responsibility.POLICY_CHANGE,
        Responsibility.BROKER_SUBMISSION,
        Responsibility.ORDER_EXECUTION,
        Responsibility.PORTFOLIO_MUTATION,
        Responsibility.CREDENTIAL_ACCESS,
        Responsibility.UNRESTRICTED_SHELL_EXECUTION,
        Responsibility.SOURCE_DELETION_WITHOUT_GOVERNED_OPERATOR_ACTION,
        Responsibility.AUTOMATIC_STRATEGY_PROMOTION,
        Responsibility.AUTOMATIC_FORECAST_DRIVEN_TRADING,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider_id: str
    execution_location: ExecutionLocation
    health: ProviderHealth = ProviderHealth.HEALTHY
    enabled: bool = True
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        validate_identifier(self.provider_id, "provider_id")
        validate_safe_metadata(self.metadata, "provider metadata")


@dataclass(frozen=True, slots=True)
class ModelRegistration:
    model_id: str
    provider_id: str
    family: str
    version: str
    capabilities: frozenset[Capability]
    execution_location: ExecutionLocation
    context_limit: int
    supported_input_types: frozenset[InputType]
    structured_output: bool
    cost_class: CostClass
    trust_tier: TrustTier
    privacy_tier: PrivacyTier
    health: ProviderHealth
    enabled: bool
    allowed_responsibilities: frozenset[Responsibility]
    prohibited_responsibilities: frozenset[Responsibility] = PROHIBITED_RESPONSIBILITIES

    def __post_init__(self) -> None:
        validate_identifier(self.model_id, "model_id")
        validate_identifier(self.provider_id, "provider_id")
        validate_identifier(self.family, "family")
        validate_identifier(self.version, "version")
        if not self.capabilities:
            raise ValueError("model registration requires capabilities")
        if self.context_limit < 1:
            raise ValueError("context_limit must be positive")
        if not self.supported_input_types:
            raise ValueError("model registration requires supported input types")
        if self.allowed_responsibilities & PROHIBITED_RESPONSIBILITIES:
            raise ValueError("models cannot be allowed prohibited Sigil responsibilities")
        if not PROHIBITED_RESPONSIBILITIES.issubset(self.prohibited_responsibilities):
            raise ValueError("all governed prohibited responsibilities must be explicit")
        if (
            self.execution_location == ExecutionLocation.LOCAL
            and self.privacy_tier < PrivacyTier.LOCAL_ONLY
        ):
            raise ValueError("local models must declare local-only privacy")
