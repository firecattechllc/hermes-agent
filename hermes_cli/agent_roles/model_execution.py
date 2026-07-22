"""Governed, provider-neutral execution of Step 26 model-routing decisions."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Mapping, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .model_routing import CandidateDisposition, RoutingDecision, RoutingPolicyOutcome


MODEL_EXECUTION_SCHEMA_VERSION = 1
_FORBIDDEN = (
    "prompt", "api_key", "api-key", "authorization:", "bearer ", "password",
    "private_key", "private key", "secret", "token=",
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _safe(value: str, field: str, maximum: int = 512) -> str:
    value = value.strip()
    if not value or len(value) > maximum or any(x in value.lower() for x in _FORBIDDEN):
        raise ValueError(f"{field} is blank, oversized, or sensitive")
    return value


def _reference(value: str, field: str) -> str:
    value = _safe(value, field)
    if not value.startswith(("artifact://", "input://", "output://")):
        raise ValueError(f"{field} must be a sanitized reference")
    return value


class ModelExecutionState(str, Enum):
    PREPARED = "prepared"
    APPROVAL_REQUIRED = "approval_required"
    ADMITTED = "admitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    FALLBACK_PENDING = "fallback_pending"
    FALLBACK_RUNNING = "fallback_running"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


class ModelExecutionErrorClass(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_PROVIDER_ERROR = "transient_provider_error"
    PERMANENT_PROVIDER_ERROR = "permanent_provider_error"
    INVALID_REQUEST = "invalid_request"
    POLICY_BLOCKED = "policy_blocked"
    APPROVAL_MISSING = "approval_missing"
    AUTHORIZATION_INVALID = "authorization_invalid"
    BUDGET_EXCEEDED = "budget_exceeded"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    CANCELLED = "cancelled"

    @property
    def retryable(self) -> bool:
        return self in {
            self.PROVIDER_UNAVAILABLE, self.TIMEOUT, self.RATE_LIMITED,
            self.TRANSIENT_PROVIDER_ERROR,
        }

    @property
    def fallback_eligible(self) -> bool:
        return self.retryable or self == self.PERMANENT_PROVIDER_ERROR


class ApprovalEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    execution_id: str
    idempotency_key: str
    routing_decision_id: str
    request_id: str
    authorized_cost_micros: int = Field(..., ge=0)
    issued_at: int = Field(..., ge=0)
    expires_at: int = Field(..., ge=0)
    revoked: bool = False

    @field_validator(
        "approval_id", "execution_id", "idempotency_key", "routing_decision_id",
        "request_id",
    )
    @classmethod
    def _safe_ids(cls, value: str, info) -> str:
        return _safe(value, info.field_name, 128)

    @model_validator(mode="after")
    def _ordered(self) -> "ApprovalEvidence":
        if self.expires_at < self.issued_at:
            raise ValueError("approval expires before issuance")
        return self


class BudgetAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: str
    execution_id: str
    idempotency_key: str
    routing_decision_id: str
    request_id: str
    authorized_cost_micros: int = Field(..., ge=0)
    issued_at: int = Field(..., ge=0)
    expires_at: int = Field(..., ge=0)
    revoked: bool = False
    approval_id: Optional[str] = None

    @field_validator(
        "authorization_id", "execution_id", "idempotency_key",
        "routing_decision_id", "request_id", "approval_id"
    )
    @classmethod
    def _safe_ids(cls, value: Optional[str], info) -> Optional[str]:
        return None if value is None else _safe(value, info.field_name, 128)

    @model_validator(mode="after")
    def _ordered(self) -> "BudgetAuthorization":
        if self.expires_at < self.issued_at:
            raise ValueError("authorization expires before issuance")
        return self


class ModelExecutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str
    idempotency_key: str
    project_id: str
    task_id: str
    request_id: str
    routing_decision: RoutingDecision
    selected_provider_id: Optional[str]
    selected_model_id: Optional[str]
    input_reference: str
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    maximum_attempts: int = Field(default=1, ge=1, le=32)
    approval: Optional[ApprovalEvidence] = None
    budget_authorization: Optional[BudgetAuthorization] = None
    requested_at: int = Field(..., ge=0)
    cancelled: bool = False

    @field_validator(
        "execution_id", "idempotency_key", "project_id", "task_id", "request_id",
        "selected_provider_id", "selected_model_id", "input_reference",
    )
    @classmethod
    def _safe_fields(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        return _reference(value, info.field_name) if info.field_name == "input_reference" else _safe(value, info.field_name)

    @model_validator(mode="after")
    def _matches_route(self) -> "ModelExecutionRequest":
        decision = self.routing_decision
        if (
            self.request_id != decision.request_id
            or self.selected_provider_id != decision.selected_provider_id
            or self.selected_model_id != decision.selected_model_id
        ):
            raise ValueError("execution request does not match routing decision")
        if self.requested_at < decision.created_at:
            raise ValueError("execution request predates routing decision")
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in encoded for marker in _FORBIDDEN):
            raise ValueError("execution request contains forbidden sensitive content")
        return self

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


class ProviderUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input_units: int = Field(..., ge=0)
    output_units: int = Field(..., ge=0)
    actual_cost_micros: int = Field(..., ge=0)


class ProviderExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    output_reference: Optional[str] = None
    usage: Optional[ProviderUsage] = None
    error_classification: Optional[ModelExecutionErrorClass] = None

    @field_validator("output_reference")
    @classmethod
    def _safe_output(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _reference(value, "output_reference")

    @model_validator(mode="after")
    def _consistent(self) -> "ProviderExecutionResult":
        if (self.error_classification is None) == (self.output_reference is None):
            raise ValueError("provider result must contain exactly one outcome")
        return self


class ModelProviderAdapter(Protocol):
    @property
    def provider_id(self) -> str: ...
    def execute(
        self, *, model_id: str, input_reference: str, timeout_seconds: int
    ) -> ProviderExecutionResult: ...


class DeterministicModelAdapter:
    """Local scripted adapter. It performs no I/O or network access."""

    def __init__(
        self, provider_id: str,
        outcomes: Mapping[str, Tuple[ProviderExecutionResult, ...]],
    ) -> None:
        self._provider_id = _safe(provider_id, "provider_id", 128)
        self._outcomes = {key: list(value) for key, value in outcomes.items()}
        self.calls: list[str] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def execute(
        self, *, model_id: str, input_reference: str, timeout_seconds: int
    ) -> ProviderExecutionResult:
        del input_reference, timeout_seconds
        self.calls.append(model_id)
        outcomes = self._outcomes.get(model_id, [])
        if not outcomes:
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.PROVIDER_UNAVAILABLE
            )
        return outcomes.pop(0)


class ModelExecutionAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    attempt: int = Field(..., ge=1)
    provider_id: str
    model_id: str
    state: ModelExecutionState
    estimated_cost_micros: int = Field(..., ge=0)
    actual_cost_micros: int = Field(..., ge=0)
    input_units: int = Field(..., ge=0)
    output_units: int = Field(..., ge=0)
    started_at: int = Field(..., ge=0)
    completed_at: int = Field(..., ge=0)
    output_reference: Optional[str] = None
    error_classification: Optional[ModelExecutionErrorClass] = None
    fallback_eligible: bool = False


class ModelExecutionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = MODEL_EXECUTION_SCHEMA_VERSION
    execution_id: str
    idempotency_key: str
    request_fingerprint: str = Field(..., min_length=64, max_length=64)
    routing_decision_id: str
    request_id: str
    project_id: str
    task_id: str
    selected_model: Optional[str]
    attempted_models: Tuple[str, ...]
    state: ModelExecutionState
    lifecycle: Tuple[ModelExecutionState, ...]
    approval_disposition: str
    authorized_cost_micros: int = Field(..., ge=0)
    estimated_cost_micros: int = Field(..., ge=0)
    actual_cost_micros: int = Field(..., ge=0)
    input_units: int = Field(..., ge=0)
    output_units: int = Field(..., ge=0)
    attempts: Tuple[ModelExecutionAttempt, ...]
    error_classification: Optional[ModelExecutionErrorClass] = None
    fallback_progression: Tuple[str, ...]
    output_reference: Optional[str] = None
    created_at: int = Field(..., ge=0)
    completed_at: int = Field(..., ge=0)
    evidence_id: str

    @model_validator(mode="after")
    def _sanitized(self) -> "ModelExecutionEvidence":
        if self.schema_version != MODEL_EXECUTION_SCHEMA_VERSION:
            raise ValueError("unsupported model execution schema version")
        if not self.lifecycle or self.lifecycle[0] != ModelExecutionState.PREPARED:
            raise ValueError("model execution lifecycle must begin prepared")
        if self.lifecycle[-1] != self.state:
            raise ValueError("model execution lifecycle terminal state mismatch")
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in encoded for marker in _FORBIDDEN):
            raise ValueError("execution evidence contains forbidden sensitive content")
        return self


class InMemoryModelExecutionStore:
    """Deterministic replay/collision boundary for locally governed executions."""

    def __init__(self) -> None:
        self._records: dict[str, ModelExecutionEvidence] = {}
        self._keys: dict[str, str] = {}

    def get(self, execution_id: str) -> Optional[ModelExecutionEvidence]:
        return self._records.get(execution_id)

    def find_by_idempotency_key(self, key: str) -> Optional[ModelExecutionEvidence]:
        execution_id = self._keys.get(key)
        return None if execution_id is None else self._records[execution_id]

    def save(self, evidence: ModelExecutionEvidence) -> ModelExecutionEvidence:
        existing = self._records.get(evidence.execution_id)
        by_key = self.find_by_idempotency_key(evidence.idempotency_key)
        if existing or by_key:
            current = existing or by_key
            if current == evidence:
                return evidence
            raise ValueError("model execution identity or idempotency collision")
        self._records[evidence.execution_id] = evidence
        self._keys[evidence.idempotency_key] = evidence.execution_id
        return evidence


class GovernedModelExecutionService:
    def __init__(
        self, adapters: Tuple[ModelProviderAdapter, ...],
        store: ModelExecutionStoreProtocol,
    ) -> None:
        self._adapters = {item.provider_id: item for item in adapters}
        if len(self._adapters) != len(adapters):
            raise ValueError("duplicate model provider adapter")
        self._store = store

    def execute(self, request: ModelExecutionRequest, *, timestamp: int) -> ModelExecutionEvidence:
        if timestamp < request.requested_at:
            raise ValueError("model execution timestamp predates request")
        prior = self._store.get(request.execution_id)
        keyed = self._store.find_by_idempotency_key(request.idempotency_key)
        if prior or keyed:
            current = prior or keyed
            if current.request_fingerprint == request.fingerprint:
                return current
            raise ValueError("conflicting model execution replay")
        state, error, approval, authorized = self._admit(request, timestamp)
        if state is not ModelExecutionState.ADMITTED:
            return self._finish(request, state, approval, authorized, (), error, timestamp)

        routes = self._routes(request.routing_decision)
        attempts = []
        cumulative = 0
        terminal_error = None
        output_reference = None
        for index, (provider_id, model_id, estimate) in enumerate(routes, 1):
            if index > request.maximum_attempts:
                terminal_error = attempts[-1].error_classification if attempts else None
                break
            if cumulative + estimate > authorized:
                terminal_error = ModelExecutionErrorClass.BUDGET_EXCEEDED
                break
            adapter = self._adapters.get(provider_id)
            result = (
                ProviderExecutionResult(error_classification=ModelExecutionErrorClass.PROVIDER_UNAVAILABLE)
                if adapter is None else adapter.execute(
                    model_id=model_id, input_reference=request.input_reference,
                    timeout_seconds=request.timeout_seconds,
                )
            )
            classification = result.error_classification
            usage = result.usage
            if classification is not None and usage is None and estimate > 0:
                classification = ModelExecutionErrorClass.OUTPUT_VALIDATION_FAILED
            if classification is None and usage is None:
                classification = ModelExecutionErrorClass.OUTPUT_VALIDATION_FAILED
            actual = 0 if usage is None else usage.actual_cost_micros
            if actual > authorized - cumulative:
                classification = ModelExecutionErrorClass.BUDGET_EXCEEDED
                result = result.model_copy(update={"output_reference": None})
            cumulative += actual
            success = classification is None
            attempt = ModelExecutionAttempt(
                attempt=index, provider_id=provider_id, model_id=model_id,
                state=ModelExecutionState.SUCCEEDED if success else ModelExecutionState.FAILED,
                estimated_cost_micros=estimate, actual_cost_micros=actual,
                input_units=0 if usage is None else usage.input_units,
                output_units=0 if usage is None else usage.output_units,
                started_at=timestamp + index - 1, completed_at=timestamp + index,
                output_reference=result.output_reference if success else None,
                error_classification=classification,
                fallback_eligible=False if classification is None else classification.fallback_eligible,
            )
            attempts.append(attempt)
            if success:
                output_reference = result.output_reference
                terminal_error = None
                break
            terminal_error = classification
            if classification is None or not classification.fallback_eligible:
                break

        if output_reference is not None:
            final_state = ModelExecutionState.SUCCEEDED
        elif request.cancelled or terminal_error == ModelExecutionErrorClass.CANCELLED:
            final_state = ModelExecutionState.CANCELLED
        elif attempts and terminal_error and terminal_error.fallback_eligible:
            final_state = ModelExecutionState.EXHAUSTED
        else:
            final_state = ModelExecutionState.FAILED
        return self._finish(
            request, final_state, approval, authorized, tuple(attempts), terminal_error,
            timestamp + max(1, len(attempts)), output_reference,
        )

    @staticmethod
    def _routes(decision: RoutingDecision) -> Tuple[Tuple[str, str, int], ...]:
        paths = () if decision.selected_model_id is None else (
            f"{decision.selected_provider_id}/{decision.selected_model_id}",
        ) + decision.fallback_chain
        candidates = {
            (item.provider_id, item.model_id): item.estimated_cost_micros
            for item in decision.candidates if item.disposition == CandidateDisposition.ELIGIBLE
        }
        routes = []
        for path in paths:
            provider, separator, model = path.partition("/")
            if not separator or (provider, model) not in candidates:
                raise ValueError("routing fallback chain is malformed or ineligible")
            routes.append((provider, model, candidates[(provider, model)]))
        return tuple(routes)

    @staticmethod
    def _admit(request, timestamp):
        decision = request.routing_decision
        if request.cancelled:
            return ModelExecutionState.CANCELLED, ModelExecutionErrorClass.CANCELLED, "cancelled", 0
        if decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE:
            return ModelExecutionState.FAILED, ModelExecutionErrorClass.POLICY_BLOCKED, "not_applicable", 0
        estimate = int(decision.estimated_cost_micros or 0)
        if decision.policy_outcome == RoutingPolicyOutcome.FREE:
            return ModelExecutionState.ADMITTED, None, "not_required", 0
        authorization = request.budget_authorization
        if authorization is None:
            classification = (
                ModelExecutionErrorClass.APPROVAL_MISSING
                if decision.approval_required else ModelExecutionErrorClass.AUTHORIZATION_INVALID
            )
            state = ModelExecutionState.APPROVAL_REQUIRED if decision.approval_required else ModelExecutionState.FAILED
            return state, classification, "required" if decision.approval_required else "invalid", 0
        if (
            authorization.routing_decision_id != decision.decision_id
            or authorization.request_id != request.request_id
            or authorization.execution_id != request.execution_id
            or authorization.idempotency_key != request.idempotency_key
            or authorization.revoked or timestamp > authorization.expires_at
            or timestamp < authorization.issued_at
            or authorization.authorized_cost_micros < estimate
            or authorization.authorized_cost_micros > decision.budget_limit_micros
        ):
            return ModelExecutionState.FAILED, ModelExecutionErrorClass.AUTHORIZATION_INVALID, "invalid", 0
        if decision.approval_required:
            approval = request.approval
            if approval is None:
                return ModelExecutionState.APPROVAL_REQUIRED, ModelExecutionErrorClass.APPROVAL_MISSING, "required", authorization.authorized_cost_micros
            if (
                approval.routing_decision_id != decision.decision_id
                or approval.request_id != request.request_id or approval.revoked
                or approval.execution_id != request.execution_id
                or approval.idempotency_key != request.idempotency_key
                or timestamp > approval.expires_at or timestamp < approval.issued_at
                or approval.authorized_cost_micros < estimate
                or authorization.approval_id != approval.approval_id
                or authorization.authorized_cost_micros > approval.authorized_cost_micros
            ):
                return ModelExecutionState.FAILED, ModelExecutionErrorClass.AUTHORIZATION_INVALID, "invalid", 0
            disposition = "approved"
        else:
            disposition = "preapproved"
        return ModelExecutionState.ADMITTED, None, disposition, authorization.authorized_cost_micros

    def _finish(
        self, request, state, approval, authorized, attempts, error, completed_at,
        output_reference=None,
    ):
        actual = sum(item.actual_cost_micros for item in attempts)
        lifecycle = [ModelExecutionState.PREPARED]
        if attempts:
            lifecycle.extend((ModelExecutionState.ADMITTED, ModelExecutionState.RUNNING))
            lifecycle.append(attempts[0].state)
            for attempt in attempts[1:]:
                lifecycle.extend((
                    ModelExecutionState.FALLBACK_PENDING,
                    ModelExecutionState.FALLBACK_RUNNING,
                    attempt.state,
                ))
            if lifecycle[-1] != state:
                lifecycle.append(state)
        else:
            lifecycle.append(state)
        identity = {
            "execution_id": request.execution_id,
            "request_fingerprint": request.fingerprint,
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "state": state.value,
        }
        evidence = ModelExecutionEvidence(
            execution_id=request.execution_id, idempotency_key=request.idempotency_key,
            request_fingerprint=request.fingerprint,
            routing_decision_id=request.routing_decision.decision_id,
            request_id=request.request_id, project_id=request.project_id,
            task_id=request.task_id,
            selected_model=None if request.selected_model_id is None else f"{request.selected_provider_id}/{request.selected_model_id}",
            attempted_models=tuple(f"{x.provider_id}/{x.model_id}" for x in attempts),
            state=state, lifecycle=tuple(lifecycle), approval_disposition=approval,
            authorized_cost_micros=authorized,
            estimated_cost_micros=(
                sum(x.estimated_cost_micros for x in attempts)
                if attempts else int(request.routing_decision.estimated_cost_micros or 0)
            ),
            actual_cost_micros=actual,
            input_units=sum(x.input_units for x in attempts),
            output_units=sum(x.output_units for x in attempts), attempts=attempts,
            error_classification=error,
            fallback_progression=tuple(f"{x.provider_id}/{x.model_id}" for x in attempts[1:]),
            output_reference=output_reference, created_at=request.requested_at,
            completed_at=completed_at,
            evidence_id=f"model_execution_{_digest(identity)[:24]}",
        )
        return self._store.save(evidence)


class ModelExecutionStoreProtocol(Protocol):
    def get(self, execution_id: str) -> Optional[ModelExecutionEvidence]: ...
    def find_by_idempotency_key(self, key: str) -> Optional[ModelExecutionEvidence]: ...
    def save(self, evidence: ModelExecutionEvidence) -> ModelExecutionEvidence: ...
