"""Route-decision evidence for Titan's OmniRoute service.

Every dispatch decision OmniRoute makes is recorded through
:class:`hermes_cli.prime.evidence.PrimeEvidenceStore` — the same append-only,
hash-chained evidence journal already used for Prime identity, admission,
and health decisions (see ``hermes_cli.prime.evidence`` module docstring).
This module does not introduce a new evidence store; it only defines what an
OmniRoute route decision looks like as one more :class:`EvidenceRecord` kind.

What gets recorded, per the governance contract: requested capability,
selected provider/model, local-vs-remote, the reason for the route, fallback
attempts, timeout/provider error, policy rejection, budget rejection, final
status, latency, and a correlation/workflow ID.

What never gets recorded: API keys, authorization headers, full prompts,
provider secrets, or raw environment-file contents. ``RouteDecisionEvidence``
enforces this the same way ``hermes_cli.agent_roles.model_execution``'s
``_FORBIDDEN`` markers do — by refusing to construct a record whose encoded
form contains one of those markers, rather than trusting every call site to
remember to redact.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hermes_cli.prime.evidence import EvidenceRecord, SensitivityTier

ROUTE_EVIDENCE_SCHEMA_VERSION = 1

# Mirrors hermes_cli.agent_roles.model_execution._FORBIDDEN -- this module
# deliberately keeps its own copy rather than importing a private name from
# another package, but the marker set and intent are identical: no secret or
# prompt content may ever be embedded in evidence.
_FORBIDDEN = (
    "prompt",
    "api_key",
    "api-key",
    "authorization:",
    "bearer ",
    "password",
    "private_key",
    "private key",
    "secret",
    "token=",
)


class RouteStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    POLICY_REJECTED = "policy_rejected"
    BUDGET_REJECTED = "budget_rejected"
    TIMED_OUT = "timed_out"


def _safe(value: str, field_name: str, maximum: int = 512) -> str:
    value = value.strip()
    if len(value) > maximum or any(marker in value.lower() for marker in _FORBIDDEN):
        raise ValueError(
            f"{field_name} is oversized or contains forbidden sensitive content"
        )
    return value


class RouteDecisionEvidence(BaseModel):
    """Sanitized, immutable evidence for a single OmniRoute dispatch decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = ROUTE_EVIDENCE_SCHEMA_VERSION
    correlation_id: str = Field(..., min_length=1, max_length=128)
    requested_capability: str = Field(..., min_length=1, max_length=128)
    selected_provider: Optional[str] = Field(default=None, max_length=64)
    selected_model: Optional[str] = Field(default=None, max_length=256)
    is_local_route: Optional[bool] = None
    reason: str = Field(..., max_length=256)
    fallback_attempts: Tuple[str, ...] = ()
    timeout_occurred: bool = False
    provider_error: Optional[str] = Field(default=None, max_length=256)
    policy_rejected: bool = False
    budget_rejected: bool = False
    status: RouteStatus
    latency_ms: int = Field(..., ge=0)
    observed_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _sanitized(self) -> "RouteDecisionEvidence":
        if self.schema_version != ROUTE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported route evidence schema version")
        if self.status == RouteStatus.SUCCEEDED and (
            self.selected_provider is None
            or self.selected_model is None
            or self.is_local_route is None
        ):
            raise ValueError(
                "a succeeded route decision requires provider, model, and locality"
            )
        if self.status == RouteStatus.POLICY_REJECTED and not self.policy_rejected:
            raise ValueError("a policy_rejected status requires policy_rejected=True")
        if self.status == RouteStatus.BUDGET_REJECTED and not self.budget_rejected:
            raise ValueError("a budget_rejected status requires budget_rejected=True")
        _safe(self.reason, "reason")
        if self.provider_error is not None:
            _safe(self.provider_error, "provider_error")
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in encoded for marker in _FORBIDDEN):
            raise ValueError(
                "route decision evidence contains forbidden sensitive content"
            )
        return self

    def redacted_summary(self) -> str:
        """A short, human-readable, secret-free summary for the outer
        :class:`hermes_cli.prime.evidence.EvidenceRecord`."""
        parts = [
            f"capability={self.requested_capability}",
            f"status={self.status.value}",
            f"provider={self.selected_provider or 'none'}",
            f"local={self.is_local_route}",
            f"latency_ms={self.latency_ms}",
        ]
        if self.fallback_attempts:
            parts.append(f"fallbacks={len(self.fallback_attempts)}")
        return " ".join(parts)


def build_route_decision_evidence_record(
    decision: RouteDecisionEvidence, *, producer_identity_id: str
) -> EvidenceRecord:
    """Wrap a :class:`RouteDecisionEvidence` as an appendable
    :class:`hermes_cli.prime.evidence.EvidenceRecord`.

    Uses ``EvidenceRecord.build`` unmodified (the same content-addressed ID
    scheme every other Prime evidence kind uses) rather than inventing a
    parallel evidence identity scheme.
    """
    return EvidenceRecord.build(
        kind="omniroute_route_decision",
        producer_identity_id=producer_identity_id,
        subject_identity_id="titan",
        provenance="hermes_cli.prime.omniroute_server",
        timestamp=decision.observed_at,
        redacted_summary=decision.redacted_summary(),
        correlation_id=decision.correlation_id,
        sensitivity=SensitivityTier.INTERNAL,
    )
