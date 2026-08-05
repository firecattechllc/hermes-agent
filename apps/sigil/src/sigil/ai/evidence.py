"""Sanitized immutable evidence for provider invocations."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import Capability, ExecutionLocation, validate_safe_metadata
from .registry import canonical_digest


@dataclass(frozen=True, slots=True)
class InvocationEvidence:
    evidence_identity: str
    request_id: str
    task_correlation_id: str
    provider_id: str
    model_id: str
    registry_revision: str
    capability: Capability
    execution_location: ExecutionLocation
    started_at: str
    ended_at: str
    succeeded: bool
    failure_classification: str | None
    input_digest: str
    output_digest: str | None
    provider_metadata: tuple[tuple[str, str], ...]
    paper_only: bool = True
    broker_submission: bool = False


def build_invocation_evidence(
    *,
    request_id: str,
    task_correlation_id: str,
    provider_id: str,
    model_id: str,
    registry_revision: str,
    capability: Capability,
    execution_location: ExecutionLocation,
    started_at: str,
    ended_at: str,
    succeeded: bool,
    failure_classification: str | None,
    input_payload: object,
    output_payload: object | None,
    provider_metadata: tuple[tuple[str, str], ...] = (),
) -> InvocationEvidence:
    validate_safe_metadata(provider_metadata, "provider metadata")
    base = InvocationEvidence(
        evidence_identity="pending",
        request_id=request_id,
        task_correlation_id=task_correlation_id,
        provider_id=provider_id,
        model_id=model_id,
        registry_revision=registry_revision,
        capability=capability,
        execution_location=execution_location,
        started_at=started_at,
        ended_at=ended_at,
        succeeded=succeeded,
        failure_classification=failure_classification,
        input_digest=f"sha256:{canonical_digest(input_payload)}",
        output_digest=None
        if output_payload is None
        else f"sha256:{canonical_digest(output_payload)}",
        provider_metadata=provider_metadata,
    )
    identity = {key: value for key, value in asdict(base).items() if key != "evidence_identity"}
    return InvocationEvidence(
        **{**identity, "evidence_identity": f"sha256:{canonical_digest(identity)}"}
    )
