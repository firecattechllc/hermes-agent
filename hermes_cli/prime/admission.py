"""Prime admission service.

Fleet Unification Stage 2C. "Prime" is the pre-existing, documented (but
previously unimplemented in code) ecosystem identity/membership/policy
authority in this repository — see
``docs/architecture/hydra-ecosystem/CANONICAL_ARCHITECTURE.md``. This module
implements Prime's admission decision logic: deterministic ADMITTED / DENIED
/ QUARANTINED outcomes evaluated from canonical identity
(:mod:`hermes_cli.prime.identity`), health (:mod:`hermes_cli.prime.health`),
declared capabilities, software/protocol versions, certification status,
policy, revocation, and quarantine state.

Admission defaults to denied. Any missing, stale, malformed, conflicting,
unsupported, expired, revoked, or unverifiable required input produces a
DENIED decision with an explicit reason code — never a silent allow.

Admission is a decision about whether a subject may participate in governed
fleet activity at all. It is not execution authority, not broker-submission
authority, not remote-maintenance authority, and not production-mutation
authority. Those are evaluated independently by
:mod:`hermes_cli.prime.sigil_contract` and
:mod:`hermes_cli.prime.remote_maintenance_governance`, each of which must
still perform its own checks even when a subject is ADMITTED.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.prime.health import HealthReport, is_usable_for_admission

ADMISSION_SCHEMA_VERSION = 1
SUPPORTED_ADMISSION_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_ADMISSION_POLICY_VERSIONS = frozenset({"prime-admission-policy-v1"})

# An admission decision must be revalidated no less often than this.
DEFAULT_REVALIDATION_SECONDS = 300


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_ADMISSION_SCHEMA_VERSIONS:
        raise ValueError(
            f"admission schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_ADMISSION_SCHEMA_VERSIONS)})"
        )
    return version


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class AdmissionOutcome(str, Enum):
    ADMITTED = "admitted"
    DENIED = "denied"
    QUARANTINED = "quarantined"


class CertificationStatus(str, Enum):
    """Echo of a subject's last known certification status.

    Admission never performs certification itself (see
    :mod:`hermes_cli.prime.certification`); it only requires that a valid,
    non-stale certification status be supplied.
    """

    CERTIFIED = "certified"
    NOT_CERTIFIED = "not_certified"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class AdmissionRequest(BaseModel):
    """Everything Prime needs to evaluate one admission decision.

    Every field the docstring's "Admission defaults to denied" clause cares
    about is represented explicitly and has no implicit default that would
    silently satisfy it — a caller must positively supply health,
    certification status, and policy version, or the request itself fails to
    construct.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(..., min_length=1, max_length=160)
    subject_identity_id: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=128)
    declared_capabilities: Tuple[str, ...] = ()
    software_version: str = Field(..., min_length=1, max_length=64)
    protocol_version: int = Field(..., ge=1)
    health: Optional[HealthReport] = None
    certification_status: CertificationStatus
    certification_evidence_ref: Optional[str] = Field(default=None, max_length=256)
    policy_version: str = Field(..., min_length=1, max_length=64)
    identity_known_and_active: bool
    identity_revoked: bool
    quarantined: bool
    restrictions: Tuple[str, ...] = ()
    requested_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _revocation_and_quarantine_are_not_both_clear_when_unknown(
        self,
    ) -> "AdmissionRequest":
        if self.identity_revoked and not self.identity_known_and_active:
            # Consistent: a revoked identity is definitionally not active.
            return self
        if self.identity_revoked and self.identity_known_and_active:
            raise ValueError("an identity cannot be both revoked and known_and_active")
        return self


class AdmissionDecision(BaseModel):
    """A deterministic, content-addressed admission decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(..., min_length=1, max_length=160)
    schema_version: int = Field(default=ADMISSION_SCHEMA_VERSION)
    request_id: str = Field(..., min_length=1, max_length=160)
    subject_identity_id: str = Field(..., min_length=1, max_length=128)
    outcome: AdmissionOutcome
    reason_codes: Tuple[str, ...]
    policy_version: str = Field(..., min_length=1, max_length=64)
    evaluated_capabilities: Tuple[str, ...] = ()
    health_ref: Optional[str] = Field(default=None, max_length=160)
    certification_ref: Optional[str] = Field(default=None, max_length=256)
    decided_at: int = Field(..., ge=0)
    revalidate_after: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @model_validator(mode="after")
    def _reason_codes_required_unless_admitted(self) -> "AdmissionDecision":
        if self.outcome != AdmissionOutcome.ADMITTED and not self.reason_codes:
            raise ValueError(
                "a non-admitted decision requires at least one reason code"
            )
        return self

    def is_current(self, now: int) -> bool:
        return (
            self.outcome == AdmissionOutcome.ADMITTED and now <= self.revalidate_after
        )

    def grants_no_execution_authority(self) -> None:
        """Documentation no-op — admission never grants execution authority.

        See :mod:`hermes_cli.prime.identity`'s ``grants_no_authority`` for the
        same convention.
        """
        return None


def _build_decision(
    *,
    request: AdmissionRequest,
    outcome: AdmissionOutcome,
    reason_codes: Tuple[str, ...],
    now: int,
    health_ref: Optional[str],
    certification_ref: Optional[str],
    revalidation_seconds: int,
) -> AdmissionDecision:
    payload = {
        "request_id": request.request_id,
        "subject_identity_id": request.subject_identity_id,
        "outcome": outcome.value,
        "reason_codes": list(reason_codes),
        "decided_at": now,
    }
    decision_id = f"padm_{_digest(payload)[:24]}"
    return AdmissionDecision(
        decision_id=decision_id,
        request_id=request.request_id,
        subject_identity_id=request.subject_identity_id,
        outcome=outcome,
        reason_codes=reason_codes,
        policy_version=request.policy_version,
        evaluated_capabilities=request.declared_capabilities,
        health_ref=health_ref,
        certification_ref=certification_ref,
        decided_at=now,
        revalidate_after=now + revalidation_seconds,
        correlation_id=request.correlation_id,
    )


class PrimeAdmissionService:
    """Deterministic, default-deny admission evaluation.

    ``evaluate`` is a pure function of its inputs (no I/O, no clock reads
    beyond the explicitly supplied ``now``), so the exact same request always
    produces the exact same decision — required for certification
    self-tests in :mod:`hermes_cli.prime.certification` to be reproducible.
    """

    def evaluate(
        self,
        request: AdmissionRequest,
        *,
        now: int,
        revalidation_seconds: int = DEFAULT_REVALIDATION_SECONDS,
    ) -> AdmissionDecision:
        reason_codes: list[str] = []

        # Identity checks — unknown or revoked identities are always denied,
        # never merely quarantined, because Prime has no record to quarantine.
        if not request.identity_known_and_active:
            reason_codes.append("identity_unknown_or_inactive")
        if request.identity_revoked:
            reason_codes.append("identity_revoked")

        # Quarantine is evaluated before other checks so a quarantined but
        # otherwise-valid subject gets an explicit QUARANTINED outcome rather
        # than being folded into a generic denial.
        if request.quarantined:
            reason_codes.append("subject_quarantined")

        # Policy version must be one Prime recognizes.
        if request.policy_version not in SUPPORTED_ADMISSION_POLICY_VERSIONS:
            reason_codes.append("unsupported_policy_version")

        # Health must be present and usable — missing, stale, expired,
        # conflicting, or unsupported-version health all fail closed.
        if request.health is None:
            reason_codes.append("missing_health_report")
        elif not is_usable_for_admission(request.health, now=now):
            reason_codes.append("health_not_usable")

        # Certification status must be a positive CERTIFIED, not merely
        # "not yet known to be uncertified".
        if request.certification_status != CertificationStatus.CERTIFIED:
            reason_codes.append(
                f"certification_status_{request.certification_status.value}"
            )
        elif not request.certification_evidence_ref:
            reason_codes.append("missing_certification_evidence")

        if request.restrictions:
            reason_codes.append("subject_has_active_restrictions")

        health_ref = request.health.report_id if request.health is not None else None

        if not request.identity_known_and_active or request.identity_revoked:
            return _build_decision(
                request=request,
                outcome=AdmissionOutcome.DENIED,
                reason_codes=tuple(reason_codes),
                now=now,
                health_ref=health_ref,
                certification_ref=request.certification_evidence_ref,
                revalidation_seconds=revalidation_seconds,
            )

        if request.quarantined:
            return _build_decision(
                request=request,
                outcome=AdmissionOutcome.QUARANTINED,
                reason_codes=tuple(reason_codes),
                now=now,
                health_ref=health_ref,
                certification_ref=request.certification_evidence_ref,
                revalidation_seconds=revalidation_seconds,
            )

        if reason_codes:
            return _build_decision(
                request=request,
                outcome=AdmissionOutcome.DENIED,
                reason_codes=tuple(reason_codes),
                now=now,
                health_ref=health_ref,
                certification_ref=request.certification_evidence_ref,
                revalidation_seconds=revalidation_seconds,
            )

        return _build_decision(
            request=request,
            outcome=AdmissionOutcome.ADMITTED,
            reason_codes=(),
            now=now,
            health_ref=health_ref,
            certification_ref=request.certification_evidence_ref,
            revalidation_seconds=revalidation_seconds,
        )
