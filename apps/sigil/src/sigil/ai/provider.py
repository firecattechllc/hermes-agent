"""Model-neutral provider protocol and deterministic test implementation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .evidence import InvocationEvidence, build_invocation_evidence
from .models import Capability, ExecutionLocation, ProviderHealth, ProviderIdentity


class ProviderFailureClass(str, Enum):
    UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    CAPABILITY_MISMATCH = "capability_mismatch"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    classification: ProviderFailureClass
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class ProviderInvocation:
    request_id: str
    task_correlation_id: str
    model_id: str
    registry_revision: str
    capability: Capability
    input_payload: Mapping[str, object]
    timeout_ms: int
    started_at: str
    ended_at: str


@dataclass(frozen=True, slots=True)
class ProviderResult:
    output: Mapping[str, object] | None
    failure: ProviderFailure | None
    evidence: InvocationEvidence
    broker_submission: bool = False
    paper_only: bool = True

    @property
    def succeeded(self) -> bool:
        return self.failure is None


class ModelProvider(Protocol):
    identity: ProviderIdentity
    model_id: str
    model_family: str
    model_version: str
    capabilities: frozenset[Capability]
    input_contract: str
    output_contract: str
    request_timeout_ms: int

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult: ...


class DeterministicProviderMode(str, Enum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"


class DeterministicProvider:
    input_contract = "application/json;schema=sigil.ai.input.v1"
    output_contract = "application/json;schema=sigil.ai.output.v1"

    def __init__(
        self,
        *,
        provider_id: str = "deterministic-local",
        model_id: str = "gemma-test",
        model_family: str = "gemma",
        model_version: str = "test-v1",
        capabilities: frozenset[Capability] = frozenset(
            {Capability.REASONING, Capability.STRUCTURED_GENERATION}
        ),
        mode: DeterministicProviderMode = DeterministicProviderMode.SUCCESS,
        request_timeout_ms: int = 1_000,
    ) -> None:
        self.identity = ProviderIdentity(provider_id, ExecutionLocation.LOCAL)
        self.model_id = model_id
        self.model_family = model_family
        self.model_version = model_version
        self.capabilities = capabilities
        self.mode = mode
        self.request_timeout_ms = request_timeout_ms

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        failure: ProviderFailure | None = None
        output: Mapping[str, object] | None = None
        if (
            self.identity.health != ProviderHealth.HEALTHY
            or self.mode == DeterministicProviderMode.UNAVAILABLE
        ):
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, "Provider unavailable.", True
            )
        elif invocation.model_id != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "Invocation model does not match the provider model identity.",
                False,
            )
        elif invocation.capability not in self.capabilities:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH,
                "Provider does not declare the requested capability.",
                False,
            )
        elif invocation.timeout_ms < 1 or self.mode == DeterministicProviderMode.TIMEOUT:
            failure = ProviderFailure(ProviderFailureClass.TIMEOUT, "Provider timed out.", True)
        elif self.mode == DeterministicProviderMode.MALFORMED_OUTPUT:
            failure = ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "Provider output failed structured validation.",
                False,
            )
        else:
            output = {
                "schema_version": 1,
                "status": "ok",
                "request_id": invocation.request_id,
                "result": "deterministic-structured-output",
            }
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=self.identity.provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=self.identity.execution_location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=failure is None,
            failure_classification=None if failure is None else failure.classification.value,
            input_payload=dict(invocation.input_payload),
            output_payload=output,
            provider_metadata=(("adapter", "deterministic-v1"),),
        )
        return ProviderResult(output=output, failure=failure, evidence=evidence)
