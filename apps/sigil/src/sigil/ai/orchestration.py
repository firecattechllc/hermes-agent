"""Bounded advisory Hermes orchestration and optional coordination surfaces."""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol

from .models import (
    PROHIBITED_RESPONSIBILITIES,
    Capability,
    CostClass,
    ExecutionLocation,
    PrivacyTier,
    ProviderHealth,
    Responsibility,
    TrustTier,
    validate_identifier,
)
from .registry import canonical_digest

ORCHESTRATION_SCHEMA_VERSION = 1
ORCHESTRATION_WORKFLOW = "governed-research-synthesis-v1"
MAX_ORCHESTRATION_STEPS = 8
MAX_PARALLELISM = 2
MAX_WORKER_OUTPUT_CHARS = 16_384
MAX_WORKER_MEMORY_MB = 256
_ZERO_HASH = "0" * 64
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ORCHESTRATION_ID = re.compile(r"^orchestration-[a-z0-9][a-z0-9._:-]{0,111}$")
_PLAN_ID = re.compile(r"^orchestration-plan-[0-9a-f]{64}$")
_STEP_ID = re.compile(r"^orchestration-step-[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^(?:analysis|evaluation)-artifact-[0-9a-f]{64}$")
_SENSITIVE = (
    "api_key",
    "api-key",
    "authorization:",
    "bearer ",
    "private_key",
    "password=",
    "secret=",
    "token=",
)
_UNSAFE_OBJECTIVES = (
    "authorize capital",
    "approve proposal",
    "change policy",
    "mutate portfolio",
    "execute order",
    "submit broker",
    "access credential",
    "shell command",
    "arbitrary network",
    "filesystem access",
    "autonomous trading",
    "self-modifying",
    "bypass confirmation",
)
_EXECUTABLE = ("#!/bin/", "subprocess.", "os.system(", "eval(", "exec(")


class OrchestrationValidationError(ValueError):
    """A governed orchestration contract failed closed."""


class OrchestrationStoreError(RuntimeError):
    """Durable orchestration state is unavailable or corrupt."""


class OrchestrationStoreConflictError(OrchestrationStoreError):
    """An immutable orchestration identity was already committed."""


class OrchestrationState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrchestrationStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class HumanInteractionKind(str, Enum):
    MISSING_EVIDENCE = "missing_evidence"
    CLARIFICATION = "clarification"
    OPTIONAL_ADVISORY_STEP = "optional_advisory_step"
    ACCEPT_RESULT = "accept_result"


class WorkerTaskType(str, Enum):
    DOCUMENT_NORMALIZATION = "document_normalization"
    EVIDENCE_TRANSFORMATION = "evidence_transformation"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    ARTIFACT_FORMAT_CONVERSION = "artifact_format_conversion"
    RESEARCH_PREPARATION = "research_preparation"


ORCHESTRATION_RESPONSIBILITIES = frozenset(
    {
        Responsibility.RESEARCH_ANALYSIS,
        Responsibility.RESEARCH_RETRIEVAL,
        Responsibility.FINANCIAL_SENTIMENT_ANALYSIS,
        Responsibility.MARKET_FORECASTING,
        Responsibility.ORCHESTRATION_SUPPORT,
        Responsibility.EVIDENCE_SUMMARIZATION,
        Responsibility.MARKET_CONTEXT,
        Responsibility.RISK_ANALYSIS,
        Responsibility.PROPOSAL_SUPPORT,
    }
)
ORCHESTRATION_CAPABILITIES = frozenset(
    {
        Capability.SEMANTIC_RETRIEVAL,
        Capability.FINANCIAL_SENTIMENT,
        Capability.TIME_SERIES_FORECASTING,
        Capability.REASONING,
        Capability.STRUCTURED_GENERATION,
    }
)


def _bounded_text(value: str, field: str, *, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise OrchestrationValidationError(f"{field} is invalid")
    lowered = value.lower()
    if any(marker in lowered for marker in (*_SENSITIVE, *_EXECUTABLE)):
        raise OrchestrationValidationError(f"{field} contains prohibited material")
    return value.strip()


def _digests(values: Sequence[str], field: str, *, required: bool = True) -> tuple[str, ...]:
    result = tuple(values)
    if (
        (required and not result)
        or len(result) > 64
        or any(_SHA256.fullmatch(item) is None for item in result)
    ):
        raise OrchestrationValidationError(f"{field} must contain bounded digest references")
    return result


def _no_authority(value: object, field: str) -> None:
    if getattr(value, "paper_only", None) is not True or any(
        getattr(value, name, False)
        for name in (
            "broker_submission",
            "execution_authorized",
            "portfolio_mutation",
            "approval_authority",
        )
    ):
        raise OrchestrationValidationError(f"{field} cannot carry execution authority")


@dataclass(frozen=True, slots=True)
class GovernedOrchestrationRequest:
    orchestration_id: str
    task_correlation_id: str
    workflow_type: str
    objective: str
    allowed_capabilities: frozenset[Capability]
    allowed_responsibilities: frozenset[Responsibility]
    required_evidence_digests: tuple[str, ...]
    privacy_requirement: PrivacyTier
    trust_requirement: TrustTier
    cost_ceiling: CostClass
    timeout_ms: int
    maximum_steps: int
    maximum_parallelism: int
    fallback_permission: bool
    human_approval_requirement: bool
    requested_at: str
    schema_version: int = ORCHESTRATION_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema_version != ORCHESTRATION_SCHEMA_VERSION
            or _ORCHESTRATION_ID.fullmatch(self.orchestration_id) is None
        ):
            raise OrchestrationValidationError("orchestration schema or identity is invalid")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        if self.workflow_type != ORCHESTRATION_WORKFLOW:
            raise OrchestrationValidationError("orchestration workflow is unsupported")
        objective = _bounded_text(self.objective, "orchestration objective")
        if any(marker in objective.lower() for marker in _UNSAFE_OBJECTIVES):
            raise OrchestrationValidationError(
                "orchestration objective requests prohibited authority"
            )
        if (
            not self.allowed_capabilities
            or not self.allowed_capabilities <= ORCHESTRATION_CAPABILITIES
        ):
            raise OrchestrationValidationError("orchestration capabilities are unsupported")
        if (
            not self.allowed_responsibilities
            or not self.allowed_responsibilities <= ORCHESTRATION_RESPONSIBILITIES
            or self.allowed_responsibilities & PROHIBITED_RESPONSIBILITIES
        ):
            raise OrchestrationValidationError("orchestration responsibilities are prohibited")
        _digests(self.required_evidence_digests, "orchestration evidence")
        if not 100 <= self.timeout_ms <= 300_000:
            raise OrchestrationValidationError("orchestration timeout is invalid")
        if not 1 <= self.maximum_steps <= MAX_ORCHESTRATION_STEPS:
            raise OrchestrationValidationError("orchestration step bound is invalid")
        if not 1 <= self.maximum_parallelism <= min(MAX_PARALLELISM, self.maximum_steps):
            raise OrchestrationValidationError("orchestration parallelism is invalid")
        if not self.requested_at:
            raise OrchestrationValidationError("orchestration timestamp is required")
        _no_authority(self, "orchestration request")


@dataclass(frozen=True, slots=True)
class GovernedOrchestrationStep:
    step_id: str
    ordinal: int
    capability: Capability
    responsibility: Responsibility
    dependencies: tuple[str, ...]
    input_digests: tuple[str, ...]
    expected_output_schema: str
    preferred_model_family: str
    timeout_ms: int
    fallback_allowed: bool
    maximum_retries: int
    requires_human_interaction: bool = False
    worker_task_type: WorkerTaskType | None = None
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if _STEP_ID.fullmatch(self.step_id) is None or self.ordinal < 1:
            raise OrchestrationValidationError("orchestration step identity is invalid")
        if (
            self.capability not in ORCHESTRATION_CAPABILITIES
            or self.responsibility not in ORCHESTRATION_RESPONSIBILITIES
        ):
            raise OrchestrationValidationError("orchestration step is not advisory")
        if self.responsibility in PROHIBITED_RESPONSIBILITIES:
            raise OrchestrationValidationError("orchestration step responsibility is prohibited")
        if any(_STEP_ID.fullmatch(item) is None for item in self.dependencies):
            raise OrchestrationValidationError("orchestration dependency is invalid")
        _digests(self.input_digests, "orchestration step input")
        if not self.expected_output_schema.startswith("sigil.ai.output."):
            raise OrchestrationValidationError("orchestration output schema is invalid")
        validate_identifier(self.preferred_model_family, "preferred_model_family")
        if not 100 <= self.timeout_ms <= 120_000 or not 0 <= self.maximum_retries <= 1:
            raise OrchestrationValidationError("orchestration step retry or timeout is invalid")
        _no_authority(self, "orchestration step")


@dataclass(frozen=True, slots=True)
class GovernedOrchestrationPlan:
    plan_id: str
    orchestration_id: str
    steps: tuple[GovernedOrchestrationStep, ...]
    registry_revision: str
    maximum_parallelism: int
    maximum_resource_budget: int
    created_at: str
    schema_version: int = ORCHESTRATION_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            _PLAN_ID.fullmatch(self.plan_id) is None
            or _ORCHESTRATION_ID.fullmatch(self.orchestration_id) is None
        ):
            raise OrchestrationValidationError("orchestration plan identity is invalid")
        if not 1 <= len(self.steps) <= MAX_ORCHESTRATION_STEPS or tuple(
            item.ordinal for item in self.steps
        ) != tuple(range(1, len(self.steps) + 1)):
            raise OrchestrationValidationError("orchestration plan ordering is invalid")
        identities = {item.step_id for item in self.steps}
        if len(identities) != len(self.steps):
            raise OrchestrationValidationError("duplicate orchestration step identity")
        completed: set[str] = set()
        for step in self.steps:
            if not set(step.dependencies) <= completed:
                raise OrchestrationValidationError(
                    "orchestration dependency is cyclic or unordered"
                )
            completed.add(step.step_id)
        if (
            _SHA256.fullmatch(self.registry_revision) is None
            or not 1 <= self.maximum_parallelism <= MAX_PARALLELISM
            or not 1 <= self.maximum_resource_budget <= 100
        ):
            raise OrchestrationValidationError("orchestration plan budget is invalid")
        _no_authority(self, "orchestration plan")


def build_orchestration_plan(
    request: GovernedOrchestrationRequest, *, registry_revision: str, created_at: str
) -> GovernedOrchestrationPlan:
    specifications = (
        (
            Capability.SEMANTIC_RETRIEVAL,
            Responsibility.RESEARCH_RETRIEVAL,
            "embeddinggemma",
            "sigil.ai.output.semantic-retrieval.v1",
        ),
        (
            Capability.FINANCIAL_SENTIMENT,
            Responsibility.FINANCIAL_SENTIMENT_ANALYSIS,
            "finbert",
            "sigil.ai.output.financial-sentiment.v1",
        ),
        (
            Capability.TIME_SERIES_FORECASTING,
            Responsibility.MARKET_FORECASTING,
            "kronos",
            "sigil.ai.output.time-series-forecast.v1",
        ),
        (
            Capability.REASONING,
            Responsibility.RESEARCH_ANALYSIS,
            "gemma",
            "sigil.ai.output.research-synthesis.v1",
        ),
    )
    selected = [item for item in specifications if item[0] in request.allowed_capabilities]
    if not selected or len(selected) > request.maximum_steps:
        raise OrchestrationValidationError("allowed capabilities do not fit the workflow bound")
    steps: list[GovernedOrchestrationStep] = []
    specialist_ids: list[str] = []
    for ordinal, (capability, responsibility, family, schema) in enumerate(selected, 1):
        if responsibility not in request.allowed_responsibilities:
            raise OrchestrationValidationError("workflow responsibility is not allowed")
        dependencies = tuple(specialist_ids) if capability == Capability.REASONING else ()
        identity_payload = {
            "orchestration_id": request.orchestration_id,
            "ordinal": ordinal,
            "capability": capability.value,
            "responsibility": responsibility.value,
            "dependencies": dependencies,
            "input_digests": request.required_evidence_digests,
            "schema": schema,
        }
        step = GovernedOrchestrationStep(
            step_id=f"orchestration-step-{canonical_digest(identity_payload)}",
            ordinal=ordinal,
            capability=capability,
            responsibility=responsibility,
            dependencies=dependencies,
            input_digests=request.required_evidence_digests,
            expected_output_schema=schema,
            preferred_model_family=family,
            timeout_ms=min(request.timeout_ms, 120_000),
            fallback_allowed=request.fallback_permission,
            maximum_retries=1,
            requires_human_interaction=request.human_approval_requirement
            and capability == Capability.REASONING,
        )
        steps.append(step)
        if capability != Capability.REASONING:
            specialist_ids.append(step.step_id)
    values = {
        "orchestration_id": request.orchestration_id,
        "steps": [asdict(item) for item in steps],
        "registry_revision": registry_revision,
        "maximum_parallelism": request.maximum_parallelism,
        "maximum_resource_budget": len(steps) * 10,
        "created_at": created_at,
    }
    return GovernedOrchestrationPlan(
        plan_id=f"orchestration-plan-{canonical_digest(values)}",
        steps=tuple(steps),
        **{key: value for key, value in values.items() if key != "steps"},
    )


def orchestration_execution_batches(
    plan: GovernedOrchestrationPlan,
) -> tuple[tuple[GovernedOrchestrationStep, ...], ...]:
    """Return deterministic dependency-safe batches bounded by plan parallelism."""
    pending = list(plan.steps)
    completed: set[str] = set()
    batches: list[tuple[GovernedOrchestrationStep, ...]] = []
    while pending:
        ready = [step for step in pending if set(step.dependencies) <= completed]
        if not ready:
            raise OrchestrationValidationError("orchestration plan contains a dependency cycle")
        batch = tuple(ready[: plan.maximum_parallelism])
        batches.append(batch)
        completed.update(step.step_id for step in batch)
        pending = [step for step in pending if step not in batch]
    return tuple(batches)


@dataclass(frozen=True, slots=True)
class GovernedStepResult:
    result_id: str
    step_id: str
    status: OrchestrationStepStatus
    artifact_id: str | None
    evidence_identities: tuple[str, ...]
    findings: tuple[str, ...]
    risks: tuple[str, ...]
    disagreements: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    confidence: float | None
    freshness: str
    failure_classification: str | None
    retryable: bool
    fallback_used: bool
    attempts: int
    completed_at: str
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.result_id, "result_id")
        if _STEP_ID.fullmatch(self.step_id) is None or not 1 <= self.attempts <= 2:
            raise OrchestrationValidationError("step result identity or attempts are invalid")
        if self.artifact_id is not None and _ARTIFACT_ID.fullmatch(self.artifact_id) is None:
            raise OrchestrationValidationError("step result artifact identity is invalid")
        _digests(
            self.evidence_identities,
            "step evidence",
            required=self.status == OrchestrationStepStatus.SUCCEEDED,
        )
        for values in (
            self.findings,
            self.risks,
            self.disagreements,
            self.missing_evidence,
            self.limitations,
        ):
            if len(values) > 16:
                raise OrchestrationValidationError("step result collection exceeds its bound")
            for item in values:
                _bounded_text(item, "step result text", maximum=512)
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise OrchestrationValidationError("step confidence is invalid")
        if self.freshness not in {"current", "stale", "unknown"}:
            raise OrchestrationValidationError("step freshness is invalid")
        if (
            self.status == OrchestrationStepStatus.SUCCEEDED
            and self.failure_classification is not None
        ):
            raise OrchestrationValidationError("successful step cannot contain failure")
        _no_authority(self, "step result")


@dataclass(frozen=True, slots=True)
class GovernedHumanInteraction:
    interaction_id: str
    orchestration_id: str
    step_id: str
    kind: HumanInteractionKind
    prompt: str
    choices: tuple[str, ...]
    created_at: str
    expires_at: str
    response: str | None = None
    responded_at: str | None = None
    schema_version: int = ORCHESTRATION_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.interaction_id, "interaction_id")
        if (
            _ORCHESTRATION_ID.fullmatch(self.orchestration_id) is None
            or _STEP_ID.fullmatch(self.step_id) is None
        ):
            raise OrchestrationValidationError("interaction association is invalid")
        _bounded_text(self.prompt, "interaction prompt", maximum=512)
        if not 2 <= len(self.choices) <= 5 or len(set(self.choices)) != len(self.choices):
            raise OrchestrationValidationError("interaction choices are invalid")
        for choice in self.choices:
            _bounded_text(choice, "interaction choice", maximum=80)
        if self.response is not None and self.response not in self.choices:
            raise OrchestrationValidationError("interaction response is invalid")
        if (self.response is None) != (self.responded_at is None):
            raise OrchestrationValidationError("interaction response timestamp mismatch")
        _no_authority(self, "human interaction")


@dataclass(frozen=True, slots=True)
class WorkerRegistration:
    worker_id: str
    worker_type: str
    supported_task_types: frozenset[WorkerTaskType]
    execution_location: ExecutionLocation
    trust_tier: TrustTier
    privacy_tier: PrivacyTier
    timeout_ms: int
    maximum_memory_mb: int
    maximum_output_chars: int
    network_allowed: bool = False
    filesystem_allowed: bool = False
    shell_allowed: bool = False
    enabled: bool = False
    health: ProviderHealth = ProviderHealth.UNAVAILABLE
    credentials_available: bool = False
    broker_access: bool = False
    portfolio_access: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.worker_id, "worker_id")
        validate_identifier(self.worker_type, "worker_type")
        if (
            not self.supported_task_types
            or not 100 <= self.timeout_ms <= 120_000
            or not 16 <= self.maximum_memory_mb <= MAX_WORKER_MEMORY_MB
            or not 1 <= self.maximum_output_chars <= MAX_WORKER_OUTPUT_CHARS
        ):
            raise OrchestrationValidationError("worker registration bounds are invalid")
        if any(
            (
                self.network_allowed,
                self.filesystem_allowed,
                self.shell_allowed,
                self.credentials_available,
                self.broker_access,
                self.portfolio_access,
            )
        ):
            raise OrchestrationValidationError(
                "OpenWorker registration requests prohibited authority"
            )


@dataclass(frozen=True, slots=True)
class GovernedWorkerRequest:
    request_id: str
    orchestration_id: str
    step_id: str
    task_type: WorkerTaskType
    input_digests: tuple[str, ...]
    expected_output_schema: str
    timeout_ms: int
    maximum_memory_mb: int
    maximum_output_chars: int
    privacy_requirement: PrivacyTier
    trust_requirement: TrustTier
    requested_at: str
    recursive: bool = False
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        if (
            _ORCHESTRATION_ID.fullmatch(self.orchestration_id) is None
            or _STEP_ID.fullmatch(self.step_id) is None
        ):
            raise OrchestrationValidationError("worker request association is invalid")
        _digests(self.input_digests, "worker input")
        if (
            not self.expected_output_schema.startswith("sigil.worker.output.")
            or not 100 <= self.timeout_ms <= 120_000
            or not 16 <= self.maximum_memory_mb <= MAX_WORKER_MEMORY_MB
            or not 1 <= self.maximum_output_chars <= MAX_WORKER_OUTPUT_CHARS
            or self.recursive
        ):
            raise OrchestrationValidationError("worker request is unsafe or unbounded")
        _no_authority(self, "worker request")


@dataclass(frozen=True, slots=True)
class GovernedWorkerResult:
    result_id: str
    request_id: str
    worker_id: str
    task_type: WorkerTaskType
    input_digest: str
    output_digest: str | None
    structured_payload: tuple[tuple[str, str], ...]
    started_at: str
    ended_at: str
    succeeded: bool
    failure_classification: str | None
    limitations: tuple[str, ...]
    evidence_identity: str
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.result_id, "result_id")
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.worker_id, "worker_id")
        if (
            _SHA256.fullmatch(self.input_digest) is None
            or (self.output_digest is not None and _SHA256.fullmatch(self.output_digest) is None)
            or _SHA256.fullmatch(self.evidence_identity) is None
        ):
            raise OrchestrationValidationError("worker result evidence is invalid")
        if (
            tuple(sorted(self.structured_payload)) != self.structured_payload
            or len(str(self.structured_payload)) > MAX_WORKER_OUTPUT_CHARS
        ):
            raise OrchestrationValidationError("worker result payload is invalid")
        serialized = str(self.structured_payload).lower()
        if any(marker in serialized for marker in (*_SENSITIVE, *_EXECUTABLE)):
            raise OrchestrationValidationError("worker result contains prohibited material")
        if self.succeeded == (self.failure_classification is not None):
            raise OrchestrationValidationError("worker result status is contradictory")
        _no_authority(self, "worker result")


class GovernedOpenWorker:
    """In-process allowlisted worker boundary with no generic execution surface."""

    def __init__(
        self,
        registration: WorkerRegistration,
        handlers: Mapping[WorkerTaskType, Callable[[tuple[str, ...]], Mapping[str, str]]],
    ) -> None:
        self.registration = registration
        self._handlers = dict(handlers)
        if set(self._handlers) - set(registration.supported_task_types):
            raise OrchestrationValidationError("worker handlers exceed registration")

    def execute(self, request: GovernedWorkerRequest, *, completed_at: str) -> GovernedWorkerResult:
        classification = None
        payload: tuple[tuple[str, str], ...] = ()
        if not self.registration.enabled or self.registration.health != ProviderHealth.HEALTHY:
            classification = "worker_unavailable"
        elif (
            request.task_type not in self.registration.supported_task_types
            or request.task_type not in self._handlers
        ):
            classification = "worker_task_unsupported"
        elif (
            request.privacy_requirement > self.registration.privacy_tier
            or request.trust_requirement > self.registration.trust_tier
        ):
            classification = "worker_policy_mismatch"
        elif (
            request.timeout_ms > self.registration.timeout_ms
            or request.maximum_memory_mb > self.registration.maximum_memory_mb
            or request.maximum_output_chars > self.registration.maximum_output_chars
        ):
            classification = "worker_resource_limit"
        else:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sigil-openworker")
            try:
                future = executor.submit(self._handlers[request.task_type], request.input_digests)
                raw = future.result(timeout=request.timeout_ms / 1000)
                payload = tuple(sorted((str(key), str(value)) for key, value in raw.items()))
                if len(str(payload)) > request.maximum_output_chars:
                    classification = "worker_output_oversized"
                    payload = ()
                elif any(marker in str(payload).lower() for marker in (*_SENSITIVE, *_EXECUTABLE)):
                    classification = "worker_output_unsafe"
                    payload = ()
            except FutureTimeoutError:
                future.cancel()
                classification = "worker_timeout"
            except (KeyError, RuntimeError, TypeError, ValueError):
                classification = "worker_failed"
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        output_digest = None if classification else f"sha256:{canonical_digest(payload)}"
        evidence = f"sha256:{canonical_digest({'request': request.request_id, 'worker': self.registration.worker_id, 'output': output_digest, 'failure': classification})}"
        return GovernedWorkerResult(
            result_id=f"worker-result-{canonical_digest({'evidence': evidence})[:64]}",
            request_id=request.request_id,
            worker_id=self.registration.worker_id,
            task_type=request.task_type,
            input_digest=f"sha256:{canonical_digest(request.input_digests)}",
            output_digest=output_digest,
            structured_payload=payload,
            started_at=request.requested_at,
            ended_at=completed_at,
            succeeded=classification is None,
            failure_classification=classification,
            limitations=(
                "Worker is deterministic, local, network-disabled, shell-disabled, and filesystem-isolated.",
            ),
            evidence_identity=evidence,
        )


@dataclass(frozen=True, slots=True)
class GovernedOrchestrationArtifact:
    artifact_id: str
    orchestration_id: str
    task_correlation_id: str
    plan_id: str
    completed_step_ids: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    skipped_step_ids: tuple[str, ...]
    evidence_identities: tuple[str, ...]
    retrieval_artifact_ids: tuple[str, ...]
    sentiment_artifact_ids: tuple[str, ...]
    forecast_artifact_ids: tuple[str, ...]
    synthesis_artifact_id: str | None
    findings: tuple[str, ...]
    risks: tuple[str, ...]
    disagreements: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    confidence: float | None
    freshness: tuple[str, ...]
    created_at: str
    responsibility: Responsibility = Responsibility.ORCHESTRATION_SUPPORT
    capability: Capability = Capability.ORCHESTRATION
    schema_version: int = ORCHESTRATION_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False
    stale_after: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.artifact_id.startswith("analysis-artifact-")
            or self.capability != Capability.ORCHESTRATION
        ):
            raise OrchestrationValidationError("orchestration artifact identity is invalid")
        _digests(self.evidence_identities, "orchestration artifact evidence")
        for identities in (
            self.retrieval_artifact_ids,
            self.sentiment_artifact_ids,
            self.forecast_artifact_ids,
        ):
            if any(_ARTIFACT_ID.fullmatch(item) is None for item in identities):
                raise OrchestrationValidationError(
                    "orchestration specialist artifact identity is invalid"
                )
        if (
            self.synthesis_artifact_id is not None
            and _ARTIFACT_ID.fullmatch(self.synthesis_artifact_id) is None
        ):
            raise OrchestrationValidationError("orchestration synthesis identity is invalid")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise OrchestrationValidationError("orchestration confidence is invalid")
        _no_authority(self, "orchestration artifact")


def build_orchestration_artifact(
    request: GovernedOrchestrationRequest,
    plan: GovernedOrchestrationPlan,
    results: Sequence[GovernedStepResult],
    *,
    created_at: str,
) -> GovernedOrchestrationArtifact:
    successful = tuple(item for item in results if item.status == OrchestrationStepStatus.SUCCEEDED)
    values = {
        "orchestration_id": request.orchestration_id,
        "task_correlation_id": request.task_correlation_id,
        "plan_id": plan.plan_id,
        "completed_step_ids": tuple(item.step_id for item in successful),
        "failed_step_ids": tuple(
            item.step_id for item in results if item.status == OrchestrationStepStatus.FAILED
        ),
        "skipped_step_ids": tuple(
            item.step_id for item in results if item.status == OrchestrationStepStatus.SKIPPED
        ),
        "evidence_identities": tuple(
            sorted({evidence for item in results for evidence in item.evidence_identities})
        ),
        "retrieval_artifact_ids": tuple(
            item.artifact_id
            for item in successful
            if item.artifact_id
            and next(step for step in plan.steps if step.step_id == item.step_id).capability
            == Capability.SEMANTIC_RETRIEVAL
        ),
        "sentiment_artifact_ids": tuple(
            item.artifact_id
            for item in successful
            if item.artifact_id
            and next(step for step in plan.steps if step.step_id == item.step_id).capability
            == Capability.FINANCIAL_SENTIMENT
        ),
        "forecast_artifact_ids": tuple(
            item.artifact_id
            for item in successful
            if item.artifact_id
            and next(step for step in plan.steps if step.step_id == item.step_id).capability
            == Capability.TIME_SERIES_FORECASTING
        ),
        "synthesis_artifact_id": next(
            (
                item.artifact_id
                for item in successful
                if item.artifact_id
                and next(step for step in plan.steps if step.step_id == item.step_id).capability
                == Capability.REASONING
            ),
            None,
        ),
        "findings": tuple(item for result in successful for item in result.findings)[:32],
        "risks": tuple(item for result in successful for item in result.risks)[:32],
        "disagreements": tuple(item for result in successful for item in result.disagreements)[:32],
        "missing_evidence": tuple(item for result in results for item in result.missing_evidence)[
            :32
        ],
        "limitations": tuple(item for result in results for item in result.limitations)[:32]
        or ("Orchestration produced no specialist result.",),
        "confidence": None
        if not any(item.confidence is not None for item in successful)
        else sum(item.confidence for item in successful if item.confidence is not None)
        / sum(item.confidence is not None for item in successful),
        "freshness": tuple(sorted({item.freshness for item in results})),
        "created_at": created_at,
    }
    if not values["evidence_identities"]:
        values["evidence_identities"] = (
            f"sha256:{canonical_digest({'orchestration': request.orchestration_id, 'state': 'no-results'})}",
        )
    return GovernedOrchestrationArtifact(
        artifact_id=f"analysis-artifact-{canonical_digest(values)}", **values
    )


@dataclass(frozen=True, slots=True)
class OrchestrationEvidence:
    evidence_id: str
    orchestration_id: str
    event_type: str
    step_id: str | None
    input_digest: str
    output_digest: str | None
    failure_classification: str | None
    created_at: str
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            _SHA256.fullmatch(self.evidence_id) is None
            or _SHA256.fullmatch(self.input_digest) is None
            or (self.output_digest is not None and _SHA256.fullmatch(self.output_digest) is None)
        ):
            raise OrchestrationValidationError("orchestration evidence identity is invalid")
        _no_authority(self, "orchestration evidence")


@dataclass(frozen=True, slots=True)
class OrchestrationRecord:
    orchestration_id: str
    request: GovernedOrchestrationRequest
    plan: GovernedOrchestrationPlan
    state: OrchestrationState
    step_results: tuple[GovernedStepResult, ...]
    evidence: tuple[OrchestrationEvidence, ...]
    interactions: tuple[GovernedHumanInteraction, ...]
    worker_results: tuple[GovernedWorkerResult, ...]
    final_artifact_id: str | None
    failure_classification: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    revision: int = 1
    schema_version: int = ORCHESTRATION_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            self.orchestration_id != self.request.orchestration_id
            or self.plan.orchestration_id != self.orchestration_id
            or self.revision < 1
        ):
            raise OrchestrationValidationError("orchestration state association is invalid")
        step_ids = tuple(item.step_id for item in self.step_results)
        if len(step_ids) != len(set(step_ids)) or not set(step_ids) <= {
            item.step_id for item in self.plan.steps
        }:
            raise OrchestrationValidationError("orchestration state contains invalid step results")
        if (
            self.state in {OrchestrationState.COMPLETED, OrchestrationState.PARTIAL}
            and self.final_artifact_id is None
        ):
            raise OrchestrationValidationError("terminal orchestration is missing its artifact")
        if (
            self.state in {OrchestrationState.FAILED, OrchestrationState.CANCELLED}
            and self.failure_classification is None
        ):
            raise OrchestrationValidationError("failed orchestration requires classification")
        _no_authority(self, "orchestration state")


class DurableOrchestrationStore:
    """Hash-chained orchestration revisions, isolated from execution and portfolio state."""

    def __init__(self, state_root: Path) -> None:
        if (
            not isinstance(state_root, Path)
            or not state_root.is_absolute()
            or not state_root.exists()
            or not state_root.is_dir()
            or state_root.is_symlink()
        ):
            raise OrchestrationStoreError("orchestration state root is unsafe")
        self.directory = state_root / "governed-ai-orchestration-v1"
        self.path = self.directory / "orchestrations.jsonl"
        self.lock_path = self.directory / "orchestrations.lock"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise OrchestrationStoreError("orchestration paths cannot use symlinks")
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def append(self, record: OrchestrationRecord) -> OrchestrationRecord:
        with self._locked():
            records = self._read_unlocked(recover_truncated_tail=True)
            history = [item for item in records if item.orchestration_id == record.orchestration_id]
            if (not history and record.revision != 1) or (
                history and record.revision != history[-1].revision + 1
            ):
                raise OrchestrationStoreConflictError("orchestration revision conflict")
            if history and history[-1].state in {
                OrchestrationState.COMPLETED,
                OrchestrationState.PARTIAL,
                OrchestrationState.FAILED,
                OrchestrationState.CANCELLED,
            }:
                raise OrchestrationStoreConflictError("terminal orchestration is immutable")
            envelope = {
                "store_version": ORCHESTRATION_SCHEMA_VERSION,
                "sequence": len(records) + 1,
                "previous_entry_hash": self._last_hash if records else _ZERO_HASH,
                "record": self._record_payload(record),
                "entry_hash": "",
            }
            envelope["entry_hash"] = canonical_digest(
                {key: value for key, value in envelope.items() if key != "entry_hash"}
            )
            encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
            try:
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OrchestrationStoreError("orchestration write made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return record

    def read_records(
        self, *, recover_truncated_tail: bool = True
    ) -> tuple[OrchestrationRecord, ...]:
        with self._locked():
            return self._read_unlocked(recover_truncated_tail=recover_truncated_tail)

    def latest(self, orchestration_id: str) -> OrchestrationRecord | None:
        matches = [
            item for item in self.read_records() if item.orchestration_id == orchestration_id
        ]
        return None if not matches else matches[-1]

    def latest_all(self) -> tuple[OrchestrationRecord, ...]:
        latest: dict[str, OrchestrationRecord] = {}
        for record in self.read_records():
            latest[record.orchestration_id] = record
        return tuple(sorted(latest.values(), key=lambda item: item.orchestration_id))

    def _read_unlocked(self, *, recover_truncated_tail: bool) -> tuple[OrchestrationRecord, ...]:
        if not self.path.exists():
            self._last_hash = _ZERO_HASH
            return ()
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if not recover_truncated_tail:
                raise OrchestrationStoreError("orchestration store has a truncated tail")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.ftruncate(descriptor, boundary)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            raw = raw[:boundary]
        records: list[OrchestrationRecord] = []
        previous = _ZERO_HASH
        latest: dict[str, OrchestrationRecord] = {}
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                envelope = json.loads(line)
                expected = canonical_digest(
                    {key: value for key, value in envelope.items() if key != "entry_hash"}
                )
                if (
                    envelope["store_version"] != ORCHESTRATION_SCHEMA_VERSION
                    or envelope["sequence"] != number
                    or envelope["previous_entry_hash"] != previous
                    or envelope["entry_hash"] != expected
                ):
                    raise OrchestrationStoreError("orchestration hash chain is invalid")
                record = self._decode_record(envelope["record"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise OrchestrationStoreError(f"corrupt orchestration record {number}") from error
            prior = latest.get(record.orchestration_id)
            if (prior is None and record.revision != 1) or (
                prior is not None and record.revision != prior.revision + 1
            ):
                raise OrchestrationStoreError("orchestration revision chain is invalid")
            latest[record.orchestration_id] = record
            records.append(record)
            previous = envelope["entry_hash"]
        self._last_hash = previous
        return tuple(records)

    @staticmethod
    def _record_payload(record: OrchestrationRecord) -> dict[str, object]:
        payload = asdict(record)
        payload["state"] = record.state.value
        payload["request"]["allowed_capabilities"] = sorted(
            item.value for item in record.request.allowed_capabilities
        )
        payload["request"]["allowed_responsibilities"] = sorted(
            item.value for item in record.request.allowed_responsibilities
        )
        payload["request"]["privacy_requirement"] = record.request.privacy_requirement.value
        payload["request"]["trust_requirement"] = record.request.trust_requirement.value
        payload["request"]["cost_ceiling"] = record.request.cost_ceiling.value
        for index, step in enumerate(record.plan.steps):
            payload["plan"]["steps"][index]["capability"] = step.capability.value
            payload["plan"]["steps"][index]["responsibility"] = step.responsibility.value
            payload["plan"]["steps"][index]["worker_task_type"] = (
                None if step.worker_task_type is None else step.worker_task_type.value
            )
        for index, result in enumerate(record.step_results):
            payload["step_results"][index]["status"] = result.status.value
        for index, interaction in enumerate(record.interactions):
            payload["interactions"][index]["kind"] = interaction.kind.value
        for index, result in enumerate(record.worker_results):
            payload["worker_results"][index]["task_type"] = result.task_type.value
        return payload

    @staticmethod
    def _decode_record(payload: Mapping[str, object]) -> OrchestrationRecord:
        request_payload = dict(payload["request"])
        request = GovernedOrchestrationRequest(
            **{
                **request_payload,
                "allowed_capabilities": frozenset(
                    Capability(item) for item in request_payload["allowed_capabilities"]
                ),
                "allowed_responsibilities": frozenset(
                    Responsibility(item) for item in request_payload["allowed_responsibilities"]
                ),
                "required_evidence_digests": tuple(request_payload["required_evidence_digests"]),
                "privacy_requirement": PrivacyTier(request_payload["privacy_requirement"]),
                "trust_requirement": TrustTier(request_payload["trust_requirement"]),
                "cost_ceiling": CostClass(request_payload["cost_ceiling"]),
            }
        )
        plan_payload = dict(payload["plan"])
        plan = GovernedOrchestrationPlan(
            **{
                **plan_payload,
                "steps": tuple(
                    GovernedOrchestrationStep(
                        **{
                            **item,
                            "capability": Capability(item["capability"]),
                            "responsibility": Responsibility(item["responsibility"]),
                            "dependencies": tuple(item["dependencies"]),
                            "input_digests": tuple(item["input_digests"]),
                            "worker_task_type": None
                            if item["worker_task_type"] is None
                            else WorkerTaskType(item["worker_task_type"]),
                        }
                    )
                    for item in plan_payload["steps"]
                ),
            }
        )
        return OrchestrationRecord(
            **{
                **payload,
                "request": request,
                "plan": plan,
                "state": OrchestrationState(payload["state"]),
                "step_results": tuple(
                    GovernedStepResult(
                        **{
                            **item,
                            "status": OrchestrationStepStatus(item["status"]),
                            "evidence_identities": tuple(item["evidence_identities"]),
                            "findings": tuple(item["findings"]),
                            "risks": tuple(item["risks"]),
                            "disagreements": tuple(item["disagreements"]),
                            "missing_evidence": tuple(item["missing_evidence"]),
                            "limitations": tuple(item["limitations"]),
                        }
                    )
                    for item in payload["step_results"]
                ),
                "evidence": tuple(OrchestrationEvidence(**item) for item in payload["evidence"]),
                "interactions": tuple(
                    GovernedHumanInteraction(
                        **{
                            **item,
                            "kind": HumanInteractionKind(item["kind"]),
                            "choices": tuple(item["choices"]),
                        }
                    )
                    for item in payload["interactions"]
                ),
                "worker_results": tuple(
                    GovernedWorkerResult(
                        **{
                            **item,
                            "task_type": WorkerTaskType(item["task_type"]),
                            "structured_payload": tuple(
                                tuple(pair) for pair in item["structured_payload"]
                            ),
                            "limitations": tuple(item["limitations"]),
                        }
                    )
                    for item in payload["worker_results"]
                ),
            }
        )


class SpecialistStepExecutor(Protocol):
    def execute(
        self,
        step: GovernedOrchestrationStep,
        request: GovernedOrchestrationRequest,
        *,
        attempt: int,
        completed_at: str,
    ) -> GovernedStepResult: ...


class ArtifactStore(Protocol):
    def append(self, artifact: GovernedOrchestrationArtifact) -> object: ...


@dataclass(frozen=True, slots=True)
class GovernedOrchestrationResponse:
    orchestration_id: str
    plan_id: str
    terminal_status: OrchestrationState
    step_summaries: tuple[GovernedStepResult, ...]
    artifact_id: str | None
    evidence_identities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    routing_summary: str
    failure_classification: str | None
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False


class GovernedOrchestrationService:
    """One bounded research workflow; specialist execution stays behind backend adapters."""

    _TRANSIENT = frozenset(
        {"timeout", "provider_unavailable", "worker_unavailable", "communication_unavailable"}
    )

    def __init__(
        self,
        *,
        store: DurableOrchestrationStore,
        artifact_store: ArtifactStore,
        specialist_executor: SpecialistStepExecutor,
        registry_revision: str,
        enabled: bool = False,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.specialist_executor = specialist_executor
        self.registry_revision = registry_revision
        self.enabled = enabled

    def run(
        self, request: GovernedOrchestrationRequest, *, completed_at: str
    ) -> GovernedOrchestrationResponse:
        plan = build_orchestration_plan(
            request, registry_revision=self.registry_revision, created_at=request.requested_at
        )
        if not self.enabled:
            return GovernedOrchestrationResponse(
                request.orchestration_id,
                plan.plan_id,
                OrchestrationState.FAILED,
                (),
                None,
                (),
                tuple(item.value for item in request.allowed_capabilities),
                ("Hermes orchestration is disabled.",),
                "orchestration disabled",
                "service_disabled",
            )
        if self.store.latest(request.orchestration_id) is not None:
            raise OrchestrationStoreConflictError("duplicate orchestration identity")
        request_evidence = OrchestrationEvidence(
            f"sha256:{canonical_digest({'request': asdict(request)})}",
            request.orchestration_id,
            "request_validated",
            None,
            f"sha256:{canonical_digest(asdict(request))}",
            None,
            None,
            request.requested_at,
        )
        plan_evidence = OrchestrationEvidence(
            f"sha256:{canonical_digest({'plan': asdict(plan)})}",
            request.orchestration_id,
            "plan_validated",
            None,
            f"sha256:{canonical_digest(asdict(request))}",
            f"sha256:{canonical_digest(asdict(plan))}",
            None,
            request.requested_at,
        )
        initial = OrchestrationRecord(
            request.orchestration_id,
            request,
            plan,
            OrchestrationState.PLANNED,
            (),
            (request_evidence, plan_evidence),
            (),
            (),
            None,
            None,
            request.requested_at,
            request.requested_at,
            None,
        )
        self.store.append(initial)
        running = replace(
            initial, state=OrchestrationState.RUNNING, revision=2, updated_at=request.requested_at
        )
        self.store.append(running)
        results: list[GovernedStepResult] = []
        missing: list[str] = []
        for step in plan.steps:
            dependency_failures = {
                item.step_id for item in results if item.status != OrchestrationStepStatus.SUCCEEDED
            }
            if set(step.dependencies) & dependency_failures:
                result = GovernedStepResult(
                    f"step-result-{canonical_digest({'step': step.step_id, 'state': 'dependency-skipped'})[:64]}",
                    step.step_id,
                    OrchestrationStepStatus.SKIPPED,
                    None,
                    (),
                    (),
                    (),
                    (),
                    ("dependency_failed",),
                    ("Step skipped because governed dependencies were unavailable.",),
                    None,
                    "unknown",
                    "dependency_failed",
                    False,
                    False,
                    1,
                    completed_at,
                )
                results.append(result)
                missing.append(step.capability.value)
                continue
            if step.requires_human_interaction:
                interaction = GovernedHumanInteraction(
                    interaction_id=f"human-interaction-{canonical_digest({'orchestration': request.orchestration_id, 'step': step.step_id})[:64]}",
                    orchestration_id=request.orchestration_id,
                    step_id=step.step_id,
                    kind=HumanInteractionKind.OPTIONAL_ADVISORY_STEP,
                    prompt="Proceed with the optional advisory synthesis step?",
                    choices=("proceed", "skip"),
                    created_at=completed_at,
                    expires_at=(
                        datetime.fromisoformat(completed_at) + timedelta(hours=1)
                    ).isoformat(),
                )
                paused = replace(
                    running,
                    state=OrchestrationState.PAUSED,
                    step_results=tuple(results),
                    evidence=(
                        *running.evidence,
                        OrchestrationEvidence(
                            f"sha256:{canonical_digest({'interaction': interaction.interaction_id})}",
                            request.orchestration_id,
                            "human_interaction_requested",
                            step.step_id,
                            f"sha256:{canonical_digest(step.input_digests)}",
                            f"sha256:{canonical_digest(interaction.interaction_id)}",
                            None,
                            completed_at,
                        ),
                    ),
                    interactions=(interaction,),
                    updated_at=completed_at,
                    revision=3,
                )
                self.store.append(paused)
                return self._response(paused, missing)
            result = self.specialist_executor.execute(
                step, request, attempt=1, completed_at=completed_at
            )
            if (
                result.status == OrchestrationStepStatus.FAILED
                and result.retryable
                and result.failure_classification in self._TRANSIENT
                and step.maximum_retries == 1
            ):
                result = self.specialist_executor.execute(
                    step, request, attempt=2, completed_at=completed_at
                )
            if result.status == OrchestrationStepStatus.FAILED:
                missing.append(step.capability.value)
                if step.fallback_allowed:
                    result = replace(
                        result,
                        status=OrchestrationStepStatus.SKIPPED,
                        fallback_used=True,
                        limitations=(
                            *result.limitations,
                            "Capability omitted under the validated fallback policy.",
                        ),
                    )
                else:
                    evidence = (
                        *running.evidence,
                        OrchestrationEvidence(
                            f"sha256:{canonical_digest({'step': step.step_id, 'failure': result.failure_classification})}",
                            request.orchestration_id,
                            "step_failed",
                            step.step_id,
                            f"sha256:{canonical_digest(step.input_digests)}",
                            None,
                            result.failure_classification,
                            completed_at,
                        ),
                    )
                    failed = replace(
                        running,
                        state=OrchestrationState.FAILED,
                        step_results=(*results, result),
                        evidence=evidence,
                        failure_classification=result.failure_classification or "step_failed",
                        updated_at=completed_at,
                        completed_at=completed_at,
                        revision=3,
                    )
                    self.store.append(failed)
                    return self._response(failed, missing)
            results.append(result)
        artifact = build_orchestration_artifact(request, plan, results, created_at=completed_at)
        self.artifact_store.append(artifact)
        state = (
            OrchestrationState.COMPLETED
            if all(item.status == OrchestrationStepStatus.SUCCEEDED for item in results)
            else OrchestrationState.PARTIAL
        )
        result_evidence = tuple(
            OrchestrationEvidence(
                f"sha256:{canonical_digest({'step': item.step_id, 'result': item.result_id})}",
                request.orchestration_id,
                "step_result",
                item.step_id,
                f"sha256:{canonical_digest(next(step.input_digests for step in plan.steps if step.step_id == item.step_id))}",
                None
                if item.artifact_id is None
                else f"sha256:{canonical_digest(item.artifact_id)}",
                item.failure_classification,
                completed_at,
            )
            for item in results
        )
        retry_and_fallback_evidence = tuple(
            OrchestrationEvidence(
                f"sha256:{canonical_digest({'step': item.step_id, 'event': event, 'result': item.result_id})}",
                request.orchestration_id,
                event,
                item.step_id,
                f"sha256:{canonical_digest(next(step.input_digests for step in plan.steps if step.step_id == item.step_id))}",
                f"sha256:{canonical_digest(item.result_id)}",
                item.failure_classification,
                completed_at,
            )
            for item in results
            for event in (
                *(("step_retried",) if item.attempts == 2 else ()),
                *(("fallback_applied",) if item.fallback_used else ()),
            )
        )
        terminal = replace(
            running,
            state=state,
            step_results=tuple(results),
            evidence=(*running.evidence, *result_evidence, *retry_and_fallback_evidence),
            final_artifact_id=artifact.artifact_id,
            updated_at=completed_at,
            completed_at=completed_at,
            revision=3,
        )
        self.store.append(terminal)
        return self._response(terminal, missing)

    def cancel(self, orchestration_id: str, *, cancelled_at: str) -> OrchestrationRecord:
        current = self.store.latest(orchestration_id)
        if current is None:
            raise KeyError(orchestration_id)
        cancelled = replace(
            current,
            state=OrchestrationState.CANCELLED,
            failure_classification="operator_cancelled",
            updated_at=cancelled_at,
            completed_at=cancelled_at,
            revision=current.revision + 1,
        )
        return self.store.append(cancelled)

    def run_hermes(
        self, work, *, requested_at: str, completed_at: str
    ) -> GovernedOrchestrationResponse:
        """Translate the narrow digest-only Hermes handoff into the governed request."""
        request = GovernedOrchestrationRequest(
            orchestration_id=work.orchestration_id,
            task_correlation_id=work.task_correlation_id,
            workflow_type=work.workflow_type,
            objective=work.objective,
            allowed_capabilities=frozenset(work.allowed_capabilities),
            allowed_responsibilities=frozenset(work.allowed_responsibilities),
            required_evidence_digests=work.evidence_context_digests,
            privacy_requirement=work.privacy_requirement,
            trust_requirement=work.trust_requirement,
            cost_ceiling=CostClass.FREE,
            timeout_ms=work.timeout_ms,
            maximum_steps=work.maximum_steps,
            maximum_parallelism=work.maximum_parallelism,
            fallback_permission=work.fallback_allowed,
            human_approval_requirement=work.human_approval_required,
            requested_at=requested_at,
        )
        return self.run(request, completed_at=completed_at)

    def respond_to_interaction(
        self, orchestration_id: str, interaction_id: str, response: str, *, responded_at: str
    ) -> OrchestrationRecord:
        current = self.store.latest(orchestration_id)
        if current is None:
            raise KeyError(orchestration_id)
        matches = [item for item in current.interactions if item.interaction_id == interaction_id]
        if len(matches) != 1 or matches[0].response is not None:
            raise OrchestrationValidationError(
                "interaction response is missing, duplicate, or expired"
            )
        try:
            expired = datetime.fromisoformat(responded_at) > datetime.fromisoformat(
                matches[0].expires_at
            )
        except ValueError as exc:
            raise OrchestrationValidationError("interaction timestamp is invalid") from exc
        if expired:
            raise OrchestrationValidationError(
                "interaction response is missing, duplicate, or expired"
            )
        updated = replace(matches[0], response=response, responded_at=responded_at)
        interactions = tuple(
            updated if item.interaction_id == interaction_id else item
            for item in current.interactions
        )
        record = replace(
            current,
            interactions=interactions,
            updated_at=responded_at,
            revision=current.revision + 1,
        )
        return self.store.append(record)

    @staticmethod
    def _response(
        record: OrchestrationRecord, missing: Sequence[str]
    ) -> GovernedOrchestrationResponse:
        return GovernedOrchestrationResponse(
            record.orchestration_id,
            record.plan.plan_id,
            record.state,
            record.step_results,
            record.final_artifact_id,
            tuple(
                sorted(
                    {
                        evidence
                        for result in record.step_results
                        for evidence in result.evidence_identities
                    }
                )
            ),
            tuple(sorted(set(missing))),
            tuple(item for result in record.step_results for item in result.limitations),
            "bounded advisory specialist workflow",
            record.failure_classification,
        )


class GovernedBuzzGateway:
    """Optional exact-identity communication projection; never a command gateway."""

    def __init__(self, *, available: bool = False) -> None:
        self.available = available
        self._messages: dict[str, tuple[str, str, str]] = {}

    def deliver(self, *, orchestration_id: str, message_type: str, content: str) -> str:
        if message_type not in {"status", "human_input", "result_summary"}:
            raise OrchestrationValidationError("Buzz message type is unsupported")
        sanitized = _bounded_text(content, "Buzz message", maximum=1_000)
        identity = f"buzz-message-{canonical_digest({'orchestration': orchestration_id, 'type': message_type, 'content': sanitized})}"
        if self.available:
            self._messages[identity] = (orchestration_id, message_type, sanitized)
        return identity

    def messages(self) -> tuple[tuple[str, tuple[str, str, str]], ...]:
        return tuple(sorted(self._messages.items()))


class GovernedAtlasProjection:
    """Read-only bounded orchestration projection."""

    def __init__(self, store: DurableOrchestrationStore, *, available: bool = False) -> None:
        self._store = store
        self.available = available

    def recent(self, limit: int = 10) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 50:
            raise OrchestrationValidationError("Atlas result limit is invalid")
        return tuple(self._summary(item) for item in self._store.latest_all()[-limit:])

    def exact(self, orchestration_id: str) -> dict[str, object] | None:
        value = self._store.latest(orchestration_id)
        return None if value is None else self._summary(value)

    @staticmethod
    def _summary(record: OrchestrationRecord) -> dict[str, object]:
        return {
            "orchestration_id": record.orchestration_id,
            "plan_id": record.plan.plan_id,
            "state": record.state.value,
            "steps": tuple((item.step_id, item.status.value) for item in record.step_results),
            "capabilities": tuple(item.capability.value for item in record.plan.steps),
            "evidence_identities": tuple(item.evidence_id for item in record.evidence),
            "artifact_id": record.final_artifact_id,
            "failure_classification": record.failure_classification,
            "limitations": tuple(
                item for result in record.step_results for item in result.limitations
            )[:20],
            "updated_at": record.updated_at,
            "paper_only": True,
            "execution_authorized": False,
            "broker_submission": False,
        }
