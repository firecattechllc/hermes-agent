"""Shared health protocol.

Fleet Unification Stage 2E. Generalizes the two existing health-adjacent
precedents already in this repository — ``hermes_cli.hermes_link.models``'s
``HermesLinkStatus``/``ComponentHealth``/``PresenceState`` (component health)
and ``sigil.ai.fleet.FleetNodeHealth`` (heartbeat freshness classification)
— into one versioned protocol.

Liveness, readiness, dependency health, degradation, quarantine, admission
validity, and certification validity are kept as distinct fields. None of
them implies any of the others: a node can be alive without being ready,
ready without being admitted, and admitted without being certified. A
"healthy" report never grants authority — it only describes observed state
as of ``observed_at``, and every consumer must independently evaluate
staleness via :func:`evaluate_health` rather than trusting the fields at
face value.

This module does not import ``sigil`` or ``hermes_cli.hermes_link`` types at
module scope; adapters accept duck-typed objects, matching the existing
``hermes_cli.mission_control.adapters.context_adapter`` convention.
"""

from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HEALTH_PROTOCOL_VERSION = 1
SUPPORTED_HEALTH_PROTOCOL_VERSIONS = frozenset({1})

# Matches sigil.ai.fleet's MAX_CLOCK_SKEW_SECONDS so the two systems cannot
# silently disagree about what counts as clock skew for the same fleet.
MAX_CLOCK_SKEW_SECONDS = 120

# Matches hermes_cli.hermes_link's implicit heartbeat cadence expectations
# (HermesLinkStatus.evidence_timestamp) and sigil.ai.fleet's
# MAX_HEARTBEAT_AGE_SECONDS.
DEFAULT_MAX_REPORT_AGE_SECONDS = 180


def _validate_protocol_version(version: int) -> int:
    if version not in SUPPORTED_HEALTH_PROTOCOL_VERSIONS:
        raise ValueError(
            f"health protocol version {version} not supported "
            f"(supported: {sorted(SUPPORTED_HEALTH_PROTOCOL_VERSIONS)})"
        )
    return version


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class LivenessState(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


class ReadinessState(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class DependencyHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class DegradationLevel(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    SEVERE = "severe"


class QuarantineState(str, Enum):
    NOT_QUARANTINED = "not_quarantined"
    QUARANTINED = "quarantined"


class ValidityState(str, Enum):
    """Tri-state echo of the last known admission/certification validity.

    A health report never *decides* admission or certification — it only
    echoes the freshness of the last known decision so a consumer can tell a
    stale echo from a live one. ``UNKNOWN`` is the fail-closed default.
    """

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class HealthCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    check_id: str = Field(..., min_length=1, max_length=128)
    passed: bool
    reason_code: Optional[str] = Field(default=None, max_length=128)
    detail: Optional[str] = Field(default=None, max_length=512)


class HealthReport(BaseModel):
    """A single, versioned, point-in-time health observation for one identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(..., min_length=1, max_length=160)
    subject_identity_id: str = Field(..., min_length=1, max_length=128)
    protocol_version: int = Field(default=HEALTH_PROTOCOL_VERSION)
    observed_at: int = Field(..., ge=0)
    expires_at: int = Field(..., ge=0)
    liveness: LivenessState
    readiness: ReadinessState
    dependency_health: Dict[str, DependencyHealth] = Field(default_factory=dict)
    degradation: DegradationLevel = DegradationLevel.NONE
    quarantine: QuarantineState = QuarantineState.NOT_QUARANTINED
    admission_validity: ValidityState = ValidityState.UNKNOWN
    certification_validity: ValidityState = ValidityState.UNKNOWN
    reason_codes: Tuple[str, ...] = ()
    checks: Tuple[HealthCheck, ...] = ()
    evidence_refs: Tuple[str, ...] = ()
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("protocol_version")
    @classmethod
    def _check_protocol_version(cls, v: int) -> int:
        return _validate_protocol_version(v)

    @model_validator(mode="after")
    def _expiry_after_observation(self) -> "HealthReport":
        if self.expires_at < self.observed_at:
            raise ValueError("a health report cannot expire before it was observed")
        return self

    @property
    def report_digest(self) -> str:
        return _digest(self.model_dump(mode="json", exclude={"report_id"}))


class HealthFinding(str, Enum):
    STALE = "stale"
    EXPIRED = "expired"
    CLOCK_SKEW = "clock_skew"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_SUBJECT = "unknown_subject"
    QUARANTINED = "quarantined"
    NOT_ALIVE = "not_alive"
    NOT_READY = "not_ready"
    CHECK_FAILED = "check_failed"


def evaluate_health(
    report: Optional[HealthReport],
    *,
    now: Optional[int] = None,
    max_age_seconds: int = DEFAULT_MAX_REPORT_AGE_SECONDS,
) -> Tuple[HealthFinding, ...]:
    """Deterministically evaluate a health report. Fail closed.

    Returns every applicable :class:`HealthFinding` (an empty tuple means
    "fresh and fully healthy"). Callers must treat any non-empty result as
    not usable for admission purposes. Findings are never collapsed into a
    single boolean so a caller cannot accidentally ignore a subset of
    failure modes.
    """
    now = int(time.time()) if now is None else now

    if report is None:
        return (HealthFinding.UNKNOWN_SUBJECT,)

    if report.protocol_version not in SUPPORTED_HEALTH_PROTOCOL_VERSIONS:
        return (HealthFinding.UNSUPPORTED_VERSION,)

    findings = []

    if report.observed_at > now + MAX_CLOCK_SKEW_SECONDS:
        findings.append(HealthFinding.CLOCK_SKEW)

    if now > report.expires_at:
        findings.append(HealthFinding.EXPIRED)
    elif now - report.observed_at > max_age_seconds:
        findings.append(HealthFinding.STALE)

    if report.quarantine == QuarantineState.QUARANTINED:
        findings.append(HealthFinding.QUARANTINED)

    if report.liveness != LivenessState.ALIVE:
        findings.append(HealthFinding.NOT_ALIVE)

    if report.readiness != ReadinessState.READY:
        findings.append(HealthFinding.NOT_READY)

    if any(not check.passed for check in report.checks):
        findings.append(HealthFinding.CHECK_FAILED)

    return tuple(findings)


def is_usable_for_admission(
    report: Optional[HealthReport],
    *,
    now: Optional[int] = None,
    max_age_seconds: int = DEFAULT_MAX_REPORT_AGE_SECONDS,
) -> bool:
    """True only when the health report is fresh, alive, ready, and unquarantined.

    This is the single fail-closed gate Prime admission calls — it never
    returns ``True`` on missing, stale, conflicting, or unsupported input.
    """
    return evaluate_health(report, now=now, max_age_seconds=max_age_seconds) == ()


# ── Adapters from pre-existing health-adjacent shapes ───────────────────────


def health_from_hermes_link_status(
    status: object,
    subject_identity_id: str,
    *,
    correlation_id: Optional[str] = None,
    max_age_seconds: int = DEFAULT_MAX_REPORT_AGE_SECONDS,
) -> HealthReport:
    """Adapt a ``hermes_cli.hermes_link.models.HermesLinkStatus``-shaped object.

    ``ComponentHealth.UNKNOWN`` components map to
    :class:`DependencyHealth.UNKNOWN`, never silently to healthy — an unknown
    component is not evidence of health.
    """
    nursery_state = getattr(status, "nursery_state")
    ollama_health = getattr(status, "ollama_health")
    finbert_health = getattr(status, "finbert_health")
    memory_index_health = getattr(status, "memory_index_health")
    degraded_components = tuple(getattr(status, "degraded_components", ()))
    presence = getattr(status, "presence")
    observed_at = int(getattr(status, "evidence_timestamp"))

    dependency_health = {
        "nursery": DependencyHealth(nursery_state.value),
        "ollama": DependencyHealth(ollama_health.value),
        "finbert": DependencyHealth(finbert_health.value),
        "memory_index": DependencyHealth(memory_index_health.value),
    }
    degraded = bool(degraded_components)
    presence_value = presence.value

    return HealthReport(
        report_id=f"health_{_digest({'subject': subject_identity_id, 'observed_at': observed_at})[:24]}",
        subject_identity_id=subject_identity_id,
        observed_at=observed_at,
        expires_at=observed_at + max_age_seconds,
        liveness=(
            LivenessState.ALIVE
            if presence_value == "online"
            else LivenessState.DEAD
            if presence_value == "offline"
            else LivenessState.UNKNOWN
        ),
        readiness=(
            ReadinessState.READY
            if not degraded and presence_value == "online"
            else ReadinessState.NOT_READY
        ),
        dependency_health=dependency_health,
        degradation=DegradationLevel.PARTIAL if degraded else DegradationLevel.NONE,
        reason_codes=degraded_components,
        correlation_id=correlation_id,
    )


def health_from_sigil_fleet_node_health(
    node_health: object,
    subject_identity_id: str,
    *,
    coordinator_time: str,
    observed_at_epoch: int,
    correlation_id: Optional[str] = None,
    max_age_seconds: int = DEFAULT_MAX_REPORT_AGE_SECONDS,
) -> HealthReport:
    """Adapt a ``sigil.ai.fleet.FleetNodeHealth``-shaped object.

    Reuses the node's own ``freshness()`` classification (``current`` /
    ``stale`` / ``clock_skew`` / ``future``) as the liveness/readiness source
    rather than re-deriving staleness thresholds independently, so the two
    systems cannot silently disagree about what "stale" means for the same
    underlying observation.
    """
    freshness = node_health.freshness(coordinator_time=coordinator_time)
    fresh = freshness == "current"
    draining = bool(getattr(node_health, "maintenance", False)) or bool(
        getattr(node_health, "draining", False)
    )

    return HealthReport(
        report_id=f"health_{_digest({'subject': subject_identity_id, 'observed_at': observed_at_epoch})[:24]}",
        subject_identity_id=subject_identity_id,
        observed_at=observed_at_epoch,
        expires_at=observed_at_epoch + max_age_seconds,
        liveness=LivenessState.ALIVE if fresh else LivenessState.UNKNOWN,
        readiness=(
            ReadinessState.READY if fresh and not draining else ReadinessState.NOT_READY
        ),
        reason_codes=() if fresh else (freshness,),
        correlation_id=correlation_id,
    )
