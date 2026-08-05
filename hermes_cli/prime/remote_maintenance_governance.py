"""Remote maintenance governance.

Fleet Unification Stage 2G. Adds the governance layer that
``hermes_cli.agent_roles.remote_maintenance`` does not have today:
canonical requester/target identity, Prime admission and health
preconditions, maintenance windows, approval expiration, and revocation.

This module does not modify, replace, or re-implement
``hermes_cli.agent_roles.remote_maintenance``. It composes in front of it:
:func:`evaluate_maintenance_request` must return an ADMITTED
:class:`MaintenanceDecision` before a caller is permitted to hand a
``RepairProposal``/``RepairApproval`` pair to the existing
``GovernedMaintenanceExecutor.execute(...)`` — this module never calls that
executor and never touches the ``MaintenanceAdapter`` transport itself, so it
cannot become a second, competing execution path.

:func:`missing_approval_scopes` intentionally mirrors (rather than imports)
the private approval-matching logic already inside
``GovernedMaintenanceExecutor.execute`` — that logic is not exposed as a
public function in ``remote_maintenance.py`` — so this module can perform the
identical read-only check before execution without needing to alter that
module's public surface.

Default is denied. Missing admission, missing or unusable health, missing
approvals, an expired approval, a revoked approval, or a request outside its
declared maintenance window are all denied, never silently proceeding.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.agent_roles.remote_maintenance import (
    ApprovalScope,
    RepairApproval,
    RepairProposal,
)
from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome
from hermes_cli.prime.health import HealthReport, is_usable_for_admission

MAINTENANCE_GOVERNANCE_SCHEMA_VERSION = 1
SUPPORTED_MAINTENANCE_GOVERNANCE_SCHEMA_VERSIONS = frozenset({1})

# An approval older than this is treated as expired even if the caller never
# explicitly set ``expires_at`` below its issued time — a maintenance
# approval is not meant to be indefinitely valid.
DEFAULT_MAX_APPROVAL_AGE_SECONDS = 3600


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_MAINTENANCE_GOVERNANCE_SCHEMA_VERSIONS:
        raise ValueError(
            f"maintenance governance schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_MAINTENANCE_GOVERNANCE_SCHEMA_VERSIONS)})"
        )
    return version


def missing_approval_scopes(
    proposal: RepairProposal, approvals: Iterable[RepairApproval]
) -> frozenset:
    """Return the :class:`ApprovalScope` values required but not approved.

    Mirrors the exact matching semantics of
    ``GovernedMaintenanceExecutor.execute``'s internal approval check
    (match by ``proposal_id`` AND ``proposal_checksum``) without importing
    that private logic, so a caller can pre-flight the same check this
    module's ``evaluate_maintenance_request`` performs.
    """
    approved = {
        scope
        for item in approvals
        if item.proposal_id == proposal.proposal_id
        and item.proposal_checksum == proposal.checksum
        for scope in item.scopes
    }
    required = {scope for step in proposal.steps for scope in step.required_approvals}
    return frozenset(required - approved)


class MaintenanceWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    starts_at: int = Field(..., ge=0)
    ends_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "MaintenanceWindow":
        if self.ends_at <= self.starts_at:
            raise ValueError("a maintenance window must end after it starts")
        return self

    def contains(self, when: int) -> bool:
        return self.starts_at <= when <= self.ends_at


class ApprovalRevocation(BaseModel):
    """A revocation of a previously granted :class:`RepairApproval`.

    ``RepairApproval`` in ``remote_maintenance.py`` has no ``revoked`` field
    of its own; revocation is expressed here, out of band, so a revoked
    approval can be recognized without mutating (or being able to forge a
    mutation of) the original immutable approval record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    approval_id: str = Field(..., min_length=1, max_length=128)
    revoked_at: int = Field(..., ge=0)
    revoked_by: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=512)


class MaintenanceOutcome(str, Enum):
    ADMITTED = "admitted"
    DENIED = "denied"


class GovernedMaintenanceRequest(BaseModel):
    """A single request to perform governed remote maintenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=MAINTENANCE_GOVERNANCE_SCHEMA_VERSION)
    request_id: str = Field(..., min_length=1, max_length=160)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    requester_identity_id: str = Field(..., min_length=1, max_length=128)
    target_identity_id: str = Field(..., min_length=1, max_length=128)
    proposal: RepairProposal
    approvals: Tuple[RepairApproval, ...]
    approval_issued_at: Tuple[int, ...]
    revocations: Tuple[ApprovalRevocation, ...] = ()
    window: MaintenanceWindow
    dry_run: bool = True
    requested_at: int = Field(..., ge=0)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    def approval_ages(self, now: int) -> Tuple[int, ...]:
        return tuple(now - issued for issued in self.approval_issued_at)


class MaintenanceDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(..., min_length=1, max_length=160)
    request_id: str = Field(..., min_length=1, max_length=160)
    outcome: MaintenanceOutcome
    reason_codes: Tuple[str, ...]
    decided_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _reason_codes_required_unless_admitted(self) -> "MaintenanceDecision":
        if self.outcome != MaintenanceOutcome.ADMITTED and not self.reason_codes:
            raise ValueError(
                "a denied maintenance decision requires at least one reason code"
            )
        return self

    def grants_no_execution_authority(self) -> None:
        """Documentation no-op — an ADMITTED decision only permits the
        caller to proceed to the existing, separately-governed
        ``GovernedMaintenanceExecutor.execute(...)`` call; it does not itself
        execute anything."""
        return None


def evaluate_maintenance_request(
    request: GovernedMaintenanceRequest,
    *,
    requester_admission: Optional[AdmissionDecision],
    requester_health: Optional[HealthReport],
    target_admission: Optional[AdmissionDecision],
    target_health: Optional[HealthReport],
    now: int,
    max_approval_age_seconds: int = DEFAULT_MAX_APPROVAL_AGE_SECONDS,
) -> MaintenanceDecision:
    """Deterministically evaluate one governed maintenance request. Fail closed."""
    reason_codes: list[str] = []

    if (
        requester_admission is None
        or requester_admission.outcome != AdmissionOutcome.ADMITTED
        or not requester_admission.is_current(now)
    ):
        reason_codes.append("requester_not_admitted")

    if (
        target_admission is None
        or target_admission.outcome != AdmissionOutcome.ADMITTED
        or not target_admission.is_current(now)
    ):
        reason_codes.append("target_not_admitted")

    if not is_usable_for_admission(requester_health, now=now):
        reason_codes.append("requester_health_not_usable")

    if not is_usable_for_admission(target_health, now=now):
        reason_codes.append("target_health_not_usable")

    if not request.window.contains(request.requested_at):
        reason_codes.append("outside_maintenance_window")

    if len(request.approval_issued_at) != len(request.approvals):
        reason_codes.append("approval_issued_at_mismatch")
    else:
        for age in request.approval_ages(request.requested_at):
            if age < 0:
                reason_codes.append("approval_issued_in_future")
                break
            if age > max_approval_age_seconds:
                reason_codes.append("approval_expired")
                break

    revoked_ids = {item.approval_id for item in request.revocations}
    if revoked_ids & {item.approval_id for item in request.approvals}:
        reason_codes.append("approval_revoked")

    missing = missing_approval_scopes(request.proposal, request.approvals)
    if missing:
        reason_codes.append("missing_required_approval_scopes")

    outcome = MaintenanceOutcome.DENIED if reason_codes else MaintenanceOutcome.ADMITTED

    payload = {
        "request_id": request.request_id,
        "outcome": outcome.value,
        "reason_codes": reason_codes,
        "decided_at": now,
    }
    decision_id = f"pmnt_{_digest(payload)[:24]}"

    return MaintenanceDecision(
        decision_id=decision_id,
        request_id=request.request_id,
        outcome=outcome,
        reason_codes=tuple(reason_codes),
        decided_at=now,
    )
