"""Governed, non-executing Self-Evolution Framework.

Stage 9 models improvement opportunities, proposals, experiments, risks,
budgets, rollback plans, independent reviews, results, promotion readiness,
and append-only lifecycle evidence.

The framework cannot modify code, execute experiments, install dependencies,
change prompts, models, routes, policy, commit or push Git changes, open pull
requests, approve itself, promote changes, access credentials, or spend money.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import AuthorityDenials

SELF_EVOLUTION_SCHEMA_VERSION = 1
_MAX_DIFF_CONTENT_BYTES = 500_000

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?<![A-Za-z0-9])(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s:=\"'\[])(?:/Users/|/home/|/root/|~[/\\]|"
    r"[A-Za-z]:\\Users\\)"
)
_PRIVATE_ENDPOINT = re.compile(
    r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?"
)
_ZERO_HASH = "sha256:" + "0" * 64


class SelfEvolutionValidationError(ValueError):
    """Self-evolution data failed closed."""


class ImprovementCategory(str, Enum):
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    COST = "cost"
    QUALITY = "quality"
    SECURITY = "security"
    OPERABILITY = "operability"
    GOVERNANCE = "governance"
    CAPABILITY = "capability"


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class ProposalState(str, Enum):
    DRAFT = "draft"
    EVIDENCE_PENDING = "evidence_pending"
    READY_FOR_REVIEW = "ready_for_review"
    UNDER_REVIEW = "under_review"
    CHANGES_REQUESTED = "changes_requested"
    EXPERIMENT_APPROVED = "experiment_approved"
    EXPERIMENT_REJECTED = "experiment_rejected"
    EXPERIMENT_RECORDED = "experiment_recorded"
    CERTIFICATION_PENDING = "certification_pending"
    PROMOTION_READY = "promotion_ready"
    PROMOTION_REJECTED = "promotion_rejected"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class ExperimentOutcome(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    REGRESSION = "regression"
    CANCELLED = "cancelled"


class ReviewDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class PromotionReadiness(str, Enum):
    NOT_READY = "not_ready"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    REVIEW_REQUIRED = "review_required"
    EXPERIMENT_REQUIRED = "experiment_required"
    CERTIFICATION_REQUIRED = "certification_required"
    REGRESSION_BLOCKED = "regression_blocked"
    RISK_BLOCKED = "risk_blocked"
    READY = "ready"


PROPOSAL_TRANSITIONS: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.DRAFT: frozenset(
        {
            ProposalState.EVIDENCE_PENDING,
            ProposalState.READY_FOR_REVIEW,
            ProposalState.ARCHIVED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.EVIDENCE_PENDING: frozenset(
        {
            ProposalState.READY_FOR_REVIEW,
            ProposalState.ARCHIVED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.READY_FOR_REVIEW: frozenset(
        {
            ProposalState.UNDER_REVIEW,
            ProposalState.CHANGES_REQUESTED,
            ProposalState.ARCHIVED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.UNDER_REVIEW: frozenset(
        {
            ProposalState.CHANGES_REQUESTED,
            ProposalState.EXPERIMENT_APPROVED,
            ProposalState.EXPERIMENT_REJECTED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.CHANGES_REQUESTED: frozenset(
        {
            ProposalState.READY_FOR_REVIEW,
            ProposalState.ARCHIVED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.EXPERIMENT_APPROVED: frozenset(
        {
            ProposalState.EXPERIMENT_RECORDED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.EXPERIMENT_REJECTED: frozenset(
        {
            ProposalState.CHANGES_REQUESTED,
            ProposalState.ARCHIVED,
        }
    ),
    ProposalState.EXPERIMENT_RECORDED: frozenset(
        {
            ProposalState.CERTIFICATION_PENDING,
            ProposalState.PROMOTION_REJECTED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.CERTIFICATION_PENDING: frozenset(
        {
            ProposalState.PROMOTION_READY,
            ProposalState.PROMOTION_REJECTED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.PROMOTION_READY: frozenset(
        {
            ProposalState.PROMOTION_REJECTED,
            ProposalState.ARCHIVED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.PROMOTION_REJECTED: frozenset(
        {
            ProposalState.CHANGES_REQUESTED,
            ProposalState.ARCHIVED,
            ProposalState.QUARANTINED,
        }
    ),
    ProposalState.QUARANTINED: frozenset({ProposalState.ARCHIVED}),
    ProposalState.ARCHIVED: frozenset(),
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise SelfEvolutionValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise SelfEvolutionValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise SelfEvolutionValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise SelfEvolutionValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise SelfEvolutionValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise SelfEvolutionValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise SelfEvolutionValidationError(
            f"{label} must be a repository-relative reference"
        )


def _exact_decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise SelfEvolutionValidationError(
            f"{label} must be an exact decimal"
        ) from error

    if not parsed.is_finite():
        raise SelfEvolutionValidationError(f"{label} must be finite")

    return parsed


def validate_proposal_transition(
    current: ProposalState,
    requested: ProposalState,
) -> None:
    if requested not in PROPOSAL_TRANSITIONS.get(current, frozenset()):
        raise SelfEvolutionValidationError(
            f"proposal transition {current.value} -> {requested.value} is denied"
        )


@dataclass(frozen=True, slots=True)
class EvolutionFrameworkConfig:
    enabled: bool = False
    schema_version: int = SELF_EVOLUTION_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != SELF_EVOLUTION_SCHEMA_VERSION:
            raise SelfEvolutionValidationError(
                "unsupported self-evolution schema"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "self-evolution configuration")

    @property
    def can_modify_source(self) -> bool:
        return False

    @property
    def can_execute_experiment(self) -> bool:
        return False

    @property
    def can_commit(self) -> bool:
        return False

    @property
    def can_push(self) -> bool:
        return False

    @property
    def can_open_pull_request(self) -> bool:
        return False

    @property
    def can_install_dependencies(self) -> bool:
        return False

    @property
    def can_mutate_policy(self) -> bool:
        return False

    @property
    def can_promote(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class EvolutionEvidenceRef:
    evidence_id: str
    kind: str
    content_digest: str
    provenance: str
    observed_at: str
    reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence ID")
        _require_identifier(self.kind, "evidence kind")
        _require_digest(self.content_digest, "evidence content digest")
        _require_timestamp(self.observed_at, "evidence observation time")

        if not self.provenance.strip():
            raise SelfEvolutionValidationError(
                "evidence provenance is required"
            )

        _validate_sanitized(asdict(self), "self-evolution evidence")
        _require_relative_reference(self.reference, "evidence reference")


@dataclass(frozen=True, slots=True)
class ImprovementOpportunity:
    opportunity_id: str
    category: ImprovementCategory
    title: str
    problem_statement: str
    affected_components: tuple[str, ...]
    affected_integrations: tuple[str, ...]
    observed_at: str
    evidence: tuple[EvolutionEvidenceRef, ...]
    schema_version: int = SELF_EVOLUTION_SCHEMA_VERSION
    opportunity_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.opportunity_digest and self.opportunity_digest != expected:
            raise SelfEvolutionValidationError(
                "improvement opportunity digest mismatch"
            )
        if not self.opportunity_digest:
            object.__setattr__(self, "opportunity_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["category"] = self.category.value
        payload.pop("opportunity_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != SELF_EVOLUTION_SCHEMA_VERSION:
            raise SelfEvolutionValidationError(
                "unsupported improvement opportunity schema"
            )

        _require_identifier(self.opportunity_id, "opportunity ID")
        _require_timestamp(self.observed_at, "opportunity observation time")

        if not isinstance(self.category, ImprovementCategory):
            raise SelfEvolutionValidationError(
                "unknown improvement category"
            )
        if not self.title.strip():
            raise SelfEvolutionValidationError(
                "opportunity title is required"
            )
        if not self.problem_statement.strip():
            raise SelfEvolutionValidationError(
                "problem statement is required"
            )

        for values, label in (
            (self.affected_components, "affected component"),
            (self.affected_integrations, "affected integration"),
        ):
            for value in values:
                _require_identifier(value, label)
            if len(set(values)) != len(values):
                raise SelfEvolutionValidationError(f"duplicate {label}")

        if not self.evidence:
            raise SelfEvolutionValidationError(
                "improvement opportunity requires evidence"
            )
        if len({item.evidence_id for item in self.evidence}) != len(
            self.evidence
        ):
            raise SelfEvolutionValidationError(
                "duplicate opportunity evidence identity"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "improvement opportunity",
        )


@dataclass(frozen=True, slots=True)
class EvolutionBudget:
    maximum_cost_usd: str
    maximum_runtime_seconds: int
    maximum_attempts: int
    maximum_compute_units: int
    maximum_input_bytes: int
    maximum_output_bytes: int

    def __post_init__(self) -> None:
        cost = _exact_decimal(self.maximum_cost_usd, "maximum experiment cost")

        if cost < Decimal("0") or cost > Decimal("1000000"):
            raise SelfEvolutionValidationError(
                "maximum experiment cost is outside bounds"
            )
        if not 1 <= self.maximum_runtime_seconds <= 604800:
            raise SelfEvolutionValidationError(
                "experiment runtime is outside bounds"
            )
        if not 1 <= self.maximum_attempts <= 20:
            raise SelfEvolutionValidationError(
                "experiment attempt count is outside bounds"
            )
        if not 1 <= self.maximum_compute_units <= 1_000_000:
            raise SelfEvolutionValidationError(
                "compute-unit budget is outside bounds"
            )
        if not 1 <= self.maximum_input_bytes <= 1_000_000_000:
            raise SelfEvolutionValidationError(
                "experiment input-byte budget is outside bounds"
            )
        if not 1 <= self.maximum_output_bytes <= 1_000_000_000:
            raise SelfEvolutionValidationError(
                "experiment output-byte budget is outside bounds"
            )


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    risk_factors: tuple[str, ...]
    blast_radius: tuple[str, ...]
    mitigations: tuple[str, ...]
    requires_security_review: bool
    requires_financial_review: bool

    def __post_init__(self) -> None:
        if not isinstance(self.level, RiskLevel):
            raise SelfEvolutionValidationError("unknown risk level")

        for values, label in (
            (self.risk_factors, "risk factor"),
            (self.blast_radius, "blast-radius component"),
            (self.mitigations, "risk mitigation"),
        ):
            if not values:
                raise SelfEvolutionValidationError(
                    f"{label} collection cannot be empty"
                )
            if any(not value.strip() for value in values):
                raise SelfEvolutionValidationError(
                    f"{label} cannot be empty"
                )
            if len(set(values)) != len(values):
                raise SelfEvolutionValidationError(f"duplicate {label}")

        if self.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            if not self.requires_security_review:
                raise SelfEvolutionValidationError(
                    "high-risk proposals require security review"
                )


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment_id: str
    hypothesis: str
    control_description: str
    treatment_description: str
    success_metrics: tuple[str, ...]
    guardrail_metrics: tuple[str, ...]
    required_tests: tuple[str, ...]
    certification_requirements: tuple[str, ...]
    budget: EvolutionBudget
    isolated: bool
    paper_only: bool
    execution_enabled: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.experiment_id, "experiment ID")

        for value, label in (
            (self.hypothesis, "experiment hypothesis"),
            (self.control_description, "control description"),
            (self.treatment_description, "treatment description"),
        ):
            if not value.strip():
                raise SelfEvolutionValidationError(f"{label} is required")

        for values, label in (
            (self.success_metrics, "success metric"),
            (self.guardrail_metrics, "guardrail metric"),
            (self.required_tests, "required test"),
            (self.certification_requirements, "certification requirement"),
        ):
            if not values:
                raise SelfEvolutionValidationError(
                    f"{label} collection cannot be empty"
                )
            if any(not value.strip() for value in values):
                raise SelfEvolutionValidationError(
                    f"{label} cannot be empty"
                )
            if len(set(values)) != len(values):
                raise SelfEvolutionValidationError(f"duplicate {label}")

        if not self.isolated:
            raise SelfEvolutionValidationError(
                "Stage 9 experiments must be isolated"
            )
        if not self.paper_only:
            raise SelfEvolutionValidationError(
                "Stage 9 experiments must remain paper-only"
            )
        if self.execution_enabled:
            raise SelfEvolutionValidationError(
                "Stage 9 cannot enable experiment execution"
            )

        _validate_sanitized(asdict(self), "experiment plan")


@dataclass(frozen=True, slots=True)
class RollbackPlan:
    rollback_id: str
    trigger_conditions: tuple[str, ...]
    rollback_steps: tuple[str, ...]
    verification_tests: tuple[str, ...]
    maximum_recovery_seconds: int
    automatic_rollback_enabled: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.rollback_id, "rollback ID")

        for values, label in (
            (self.trigger_conditions, "rollback trigger"),
            (self.rollback_steps, "rollback step"),
            (self.verification_tests, "rollback verification test"),
        ):
            if not values:
                raise SelfEvolutionValidationError(
                    f"{label} collection cannot be empty"
                )
            if any(not value.strip() for value in values):
                raise SelfEvolutionValidationError(
                    f"{label} cannot be empty"
                )

        if not 1 <= self.maximum_recovery_seconds <= 604800:
            raise SelfEvolutionValidationError(
                "rollback recovery time is outside bounds"
            )
        if self.automatic_rollback_enabled:
            raise SelfEvolutionValidationError(
                "Stage 9 cannot enable automatic rollback execution"
            )

        _validate_sanitized(asdict(self), "rollback plan")


@dataclass(frozen=True, slots=True)
class IndependentReview:
    review_id: str
    reviewer_identity: str
    reviewed_at: str
    decision: ReviewDecision
    scope: tuple[str, ...]
    evidence_digest: str
    comments_reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.review_id, "review ID")
        _require_timestamp(self.reviewed_at, "review time")
        _require_digest(self.evidence_digest, "review evidence digest")

        if not self.reviewer_identity.strip():
            raise SelfEvolutionValidationError(
                "reviewer identity is required"
            )
        if not isinstance(self.decision, ReviewDecision):
            raise SelfEvolutionValidationError(
                "unknown review decision"
            )
        if not self.scope:
            raise SelfEvolutionValidationError(
                "review scope cannot be empty"
            )
        if any(not item.strip() for item in self.scope):
            raise SelfEvolutionValidationError(
                "review scope cannot contain empty values"
            )

        _validate_sanitized(asdict(self), "independent review")
        _require_relative_reference(
            self.comments_reference,
            "review comments reference",
        )


@dataclass(frozen=True, slots=True)
class ImprovementProposal:
    proposal_id: str
    opportunity_id: str
    opportunity_digest: str
    title: str
    summary: str
    expected_benefits: tuple[str, ...]
    affected_components: tuple[str, ...]
    affected_integrations: tuple[str, ...]
    risk: RiskAssessment
    experiment: ExperimentPlan
    rollback: RollbackPlan
    minimum_independent_reviews: int
    created_at: str
    created_by: str
    state: ProposalState = ProposalState.DRAFT
    schema_version: int = SELF_EVOLUTION_SCHEMA_VERSION
    proposal_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.proposal_digest and self.proposal_digest != expected:
            raise SelfEvolutionValidationError(
                "improvement proposal digest mismatch"
            )
        if not self.proposal_digest:
            object.__setattr__(self, "proposal_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload.pop("proposal_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != SELF_EVOLUTION_SCHEMA_VERSION:
            raise SelfEvolutionValidationError(
                "unsupported improvement proposal schema"
            )

        _require_identifier(self.proposal_id, "proposal ID")
        _require_identifier(self.opportunity_id, "opportunity ID")
        _require_digest(self.opportunity_digest, "opportunity digest")
        _require_timestamp(self.created_at, "proposal creation time")

        if not isinstance(self.state, ProposalState):
            raise SelfEvolutionValidationError(
                "unknown proposal state"
            )
        if not self.title.strip():
            raise SelfEvolutionValidationError(
                "proposal title is required"
            )
        if not self.summary.strip():
            raise SelfEvolutionValidationError(
                "proposal summary is required"
            )
        if not self.created_by.strip():
            raise SelfEvolutionValidationError(
                "proposal creator identity is required"
            )
        if not self.expected_benefits:
            raise SelfEvolutionValidationError(
                "proposal requires expected benefits"
            )
        if not 1 <= self.minimum_independent_reviews <= 10:
            raise SelfEvolutionValidationError(
                "independent review requirement is outside bounds"
            )

        for values, label in (
            (self.affected_components, "affected component"),
            (self.affected_integrations, "affected integration"),
        ):
            for value in values:
                _require_identifier(value, label)
            if len(set(values)) != len(values):
                raise SelfEvolutionValidationError(f"duplicate {label}")

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "improvement proposal",
        )

    @property
    def can_apply_change(self) -> bool:
        return False

    @property
    def can_self_approve(self) -> bool:
        return False

    @property
    def can_promote(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    result_id: str
    proposal_id: str
    proposal_digest: str
    outcome: ExperimentOutcome
    recorded_at: str
    metrics: Mapping[str, str]
    passed_tests: tuple[str, ...]
    failed_tests: tuple[str, ...]
    regression_evidence: tuple[EvolutionEvidenceRef, ...]
    runtime_seconds: int
    attempt_count: int
    compute_units: int
    input_bytes: int
    output_bytes: int
    cost_usd: str
    result_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.result_digest and self.result_digest != expected:
            raise SelfEvolutionValidationError(
                "experiment result digest mismatch"
            )
        if not self.result_digest:
            object.__setattr__(self, "result_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["outcome"] = self.outcome.value
        payload.pop("result_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        _require_identifier(self.result_id, "result ID")
        _require_identifier(self.proposal_id, "proposal ID")
        _require_digest(self.proposal_digest, "proposal digest")
        _require_timestamp(self.recorded_at, "result recording time")

        if not isinstance(self.outcome, ExperimentOutcome):
            raise SelfEvolutionValidationError(
                "unknown experiment outcome"
            )
        if not 0 <= self.runtime_seconds <= 604800:
            raise SelfEvolutionValidationError(
                "result runtime is outside bounds"
            )
        if not 0 <= self.attempt_count <= 20:
            raise SelfEvolutionValidationError(
                "result attempt count is outside bounds"
            )
        if not 0 <= self.compute_units <= 1_000_000:
            raise SelfEvolutionValidationError(
                "result compute units are outside bounds"
            )
        if not 0 <= self.input_bytes <= 1_000_000_000:
            raise SelfEvolutionValidationError(
                "result input bytes are outside bounds"
            )
        if not 0 <= self.output_bytes <= 1_000_000_000:
            raise SelfEvolutionValidationError(
                "result output bytes are outside bounds"
            )

        cost = _exact_decimal(self.cost_usd, "experiment result cost")
        if cost < Decimal("0"):
            raise SelfEvolutionValidationError(
                "experiment result cost cannot be negative"
            )

        if len(
            {item.evidence_id for item in self.regression_evidence}
        ) != len(self.regression_evidence):
            raise SelfEvolutionValidationError(
                "duplicate regression evidence identity"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "experiment result",
        )

    def validate_for(self, proposal: ImprovementProposal) -> None:
        if self.proposal_id != proposal.proposal_id:
            raise SelfEvolutionValidationError(
                "experiment result does not match proposal identity"
            )
        if self.proposal_digest != proposal.proposal_digest:
            raise SelfEvolutionValidationError(
                "experiment result does not match proposal digest"
            )

        budget = proposal.experiment.budget
        if self.runtime_seconds > budget.maximum_runtime_seconds:
            raise SelfEvolutionValidationError(
                "experiment result exceeds runtime budget"
            )
        if self.attempt_count > budget.maximum_attempts:
            raise SelfEvolutionValidationError(
                "experiment result exceeds attempt budget"
            )
        if self.compute_units > budget.maximum_compute_units:
            raise SelfEvolutionValidationError(
                "experiment result exceeds compute budget"
            )
        if self.input_bytes > budget.maximum_input_bytes:
            raise SelfEvolutionValidationError(
                "experiment result exceeds input budget"
            )
        if self.output_bytes > budget.maximum_output_bytes:
            raise SelfEvolutionValidationError(
                "experiment result exceeds output budget"
            )

        cost = _exact_decimal(self.cost_usd, "experiment result cost")
        maximum = _exact_decimal(
            budget.maximum_cost_usd,
            "maximum experiment cost",
        )
        if cost > maximum:
            raise SelfEvolutionValidationError(
                "experiment result exceeds cost budget"
            )


@dataclass(frozen=True, slots=True)
class PromotionAssessment:
    proposal_id: str
    readiness: PromotionReadiness
    evidence_complete: bool
    independent_reviews_satisfied: bool
    experiment_passed: bool
    required_tests_passed: bool
    certification_satisfied: bool
    regression_free: bool
    risk_acceptable: bool
    reason: str
    proposal_digest: str
    result_digest: str | None
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.proposal_id, "assessment proposal ID")
        _require_digest(self.proposal_digest, "proposal digest")

        if self.result_digest is not None:
            _require_digest(self.result_digest, "result digest")
        if not isinstance(self.readiness, PromotionReadiness):
            raise SelfEvolutionValidationError(
                "unknown promotion readiness"
            )
        if not self.reason.strip():
            raise SelfEvolutionValidationError(
                "promotion assessment reason is required"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "promotion assessment")

    @property
    def can_promote(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class EvolutionLifecycleEvent:
    event_id: str
    proposal_id: str
    sequence: int
    occurred_at: str
    actor_identity: str
    previous_state: ProposalState
    requested_state: ProposalState
    reason: str
    proposal_digest: str
    previous_event_digest: str
    event_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "lifecycle event ID")
        _require_identifier(self.proposal_id, "proposal ID")
        _require_timestamp(self.occurred_at, "event time")
        _require_digest(self.proposal_digest, "proposal digest")
        _require_digest(
            self.previous_event_digest,
            "previous lifecycle event digest",
        )

        if self.sequence < 0:
            raise SelfEvolutionValidationError(
                "lifecycle sequence cannot be negative"
            )
        if not self.actor_identity.strip():
            raise SelfEvolutionValidationError(
                "lifecycle actor identity is required"
            )
        if not self.reason.strip():
            raise SelfEvolutionValidationError(
                "lifecycle reason is required"
            )

        validate_proposal_transition(
            self.previous_state,
            self.requested_state,
        )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "evolution lifecycle event",
        )

        expected = self.expected_digest()
        if self.event_digest and self.event_digest != expected:
            raise SelfEvolutionValidationError(
                "lifecycle event digest mismatch"
            )
        if not self.event_digest:
            object.__setattr__(self, "event_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["previous_state"] = self.previous_state.value
        payload["requested_state"] = self.requested_state.value
        payload.pop("event_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"


def transition_proposal(
    proposal: ImprovementProposal,
    requested_state: ProposalState,
) -> ImprovementProposal:
    """Return a new proposal state without applying any proposed change."""

    validate_proposal_transition(proposal.state, requested_state)
    return replace(
        proposal,
        state=requested_state,
        proposal_digest="",
    )


def create_lifecycle_event(
    proposal: ImprovementProposal,
    *,
    event_id: str,
    sequence: int,
    occurred_at: str,
    actor_identity: str,
    requested_state: ProposalState,
    reason: str,
    previous_event_digest: str = _ZERO_HASH,
) -> EvolutionLifecycleEvent:
    return EvolutionLifecycleEvent(
        event_id=event_id,
        proposal_id=proposal.proposal_id,
        sequence=sequence,
        occurred_at=occurred_at,
        actor_identity=actor_identity,
        previous_state=proposal.state,
        requested_state=requested_state,
        reason=reason,
        proposal_digest=proposal.proposal_digest,
        previous_event_digest=previous_event_digest,
    )


def assess_promotion_readiness(
    proposal: ImprovementProposal,
    *,
    reviews: tuple[IndependentReview, ...],
    result: ExperimentResult | None,
    evidence_complete: bool,
    certification_results: Mapping[str, bool],
) -> PromotionAssessment:
    """Assess promotion readiness without granting promotion authority."""

    unique_reviewers = {
        review.reviewer_identity
        for review in reviews
        if review.decision is ReviewDecision.APPROVED
        and review.reviewer_identity != proposal.created_by
    }
    reviews_satisfied = (
        len(unique_reviewers) >= proposal.minimum_independent_reviews
    )

    if result is not None:
        result.validate_for(proposal)

    experiment_passed = (
        result is not None
        and result.outcome is ExperimentOutcome.PASSED
    )
    required_tests_passed = (
        result is not None
        and not result.failed_tests
        and set(proposal.experiment.required_tests).issubset(
            set(result.passed_tests)
        )
    )
    certification_satisfied = all(
        certification_results.get(requirement, False)
        for requirement in proposal.experiment.certification_requirements
    )
    regression_free = (
        result is not None
        and result.outcome is not ExperimentOutcome.REGRESSION
        and not result.regression_evidence
    )
    risk_acceptable = proposal.risk.level not in {
        RiskLevel.CRITICAL,
    }

    if not evidence_complete:
        readiness = PromotionReadiness.EVIDENCE_INCOMPLETE
        reason = "Proposal evidence is incomplete."
    elif not risk_acceptable:
        readiness = PromotionReadiness.RISK_BLOCKED
        reason = "Critical-risk proposal is blocked from promotion."
    elif not reviews_satisfied:
        readiness = PromotionReadiness.REVIEW_REQUIRED
        reason = "Independent review requirements are not satisfied."
    elif result is None or result.outcome is ExperimentOutcome.NOT_RUN:
        readiness = PromotionReadiness.EXPERIMENT_REQUIRED
        reason = "A governed experiment result is required."
    elif not regression_free:
        readiness = PromotionReadiness.REGRESSION_BLOCKED
        reason = "Regression evidence blocks promotion."
    elif not experiment_passed or not required_tests_passed:
        readiness = PromotionReadiness.EXPERIMENT_REQUIRED
        reason = "Experiment or required tests did not pass."
    elif not certification_satisfied:
        readiness = PromotionReadiness.CERTIFICATION_REQUIRED
        reason = "Certification requirements are incomplete."
    else:
        readiness = PromotionReadiness.READY
        reason = (
            "Proposal evidence, independent reviews, experiment results, "
            "tests, certification, rollback, and risk gates are satisfied."
        )

    return PromotionAssessment(
        proposal_id=proposal.proposal_id,
        readiness=readiness,
        evidence_complete=evidence_complete,
        independent_reviews_satisfied=reviews_satisfied,
        experiment_passed=experiment_passed,
        required_tests_passed=required_tests_passed,
        certification_satisfied=certification_satisfied,
        regression_free=regression_free,
        risk_acceptable=risk_acceptable,
        reason=reason,
        proposal_digest=proposal.proposal_digest,
        result_digest=None if result is None else result.result_digest,
    )


def produce_evidence_diff(
    *,
    old_content: str,
    new_content: str,
    old_label: str = "before",
    new_label: str = "after",
) -> str:
    """Produce a unified-diff string for governed proposal evidence.

    Hermes add-on continuation run, per operator-authorized scope (see
    ``docs/architecture/SELF_EVOLUTION_SAFETY_ANALYSIS.md``): the only
    Self-Evolution capability implemented this run. Pure and read-only in
    the strongest sense -- no filesystem access, no subprocess, no
    network, no mutation of any argument. Both ``old_content`` and
    ``new_content`` must already be in the caller's possession as plain
    strings; this function reads nothing from disk and writes nothing
    anywhere. The returned string is evidence/display text only and is
    never treated as "applied" by anything in this module --
    ``EvolutionFrameworkConfig.can_modify_source`` remains hardcoded
    ``False`` regardless of what this function returns.
    """

    for value, label in ((old_content, "old_content"), (new_content, "new_content")):
        if not isinstance(value, str):
            raise SelfEvolutionValidationError(f"{label} must be a string")
        if len(value.encode("utf-8", errors="replace")) > _MAX_DIFF_CONTENT_BYTES:
            raise SelfEvolutionValidationError(
                f"{label} exceeds the maximum diffable size ({_MAX_DIFF_CONTENT_BYTES} bytes)"
            )

    diff_lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=old_label,
        tofile=new_label,
    )
    return "".join(diff_lines)
