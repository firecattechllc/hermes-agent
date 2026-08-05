"""Governed, non-dispatching fleet routing convergence.

Stage 10 evaluates injected fleet evidence against the Stage 2 worker contract
and produces deterministic primary, fallback, and exclusion decisions.

This module performs no dispatch, provisioning, network requests, SSH,
authentication, credential resolution, shell execution, filesystem access,
installation, activation, policy mutation, or financial action.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import AuthorityDenials
from sigil.worker_contract import (
    WORKER_CONTRACT_SCHEMA_VERSION,
    GovernedWorkerJob,
)

FLEET_ROUTING_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?<![A-Za-z0-9])(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9]{8,}"
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


class FleetRoutingValidationError(ValueError):
    """Fleet routing data failed closed."""


class FleetNodeRole(str, Enum):
    PRIMARY = "primary"
    SENIOR = "senior"
    PERSISTENT_WORKER = "persistent_worker"
    SPECIALIZED_WORKER = "specialized_worker"
    STANDBY = "standby"


class FleetHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BUSY = "busy"
    STALE = "stale"
    OFFLINE = "offline"
    INCOMPATIBLE = "incompatible"
    QUARANTINED = "quarantined"


class FleetLeaseState(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    RELEASED = "released"
    INVALID = "invalid"


class FleetTrustTier(str, Enum):
    UNTRUSTED = "untrusted"
    OBSERVED = "observed"
    REVIEWED = "reviewed"
    SANDBOXED = "sandboxed"
    CERTIFIED = "certified"


class RouteEligibility(str, Enum):
    ELIGIBLE = "eligible"
    DISABLED = "disabled"
    CAPABILITY_MISMATCH = "capability_mismatch"
    MACHINE_MISMATCH = "machine_mismatch"
    PROFILE_MISMATCH = "profile_mismatch"
    HEALTH_BLOCKED = "health_blocked"
    STALE_EVIDENCE = "stale_evidence"
    LEASE_BLOCKED = "lease_blocked"
    CAPACITY_BLOCKED = "capacity_blocked"
    BUDGET_BLOCKED = "budget_blocked"
    TRUST_BLOCKED = "trust_blocked"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"


_ROLE_SCORE = {
    FleetNodeRole.PRIMARY: 500,
    FleetNodeRole.SENIOR: 400,
    FleetNodeRole.PERSISTENT_WORKER: 300,
    FleetNodeRole.SPECIALIZED_WORKER: 250,
    FleetNodeRole.STANDBY: 100,
}

_HEALTH_SCORE = {
    FleetHealthState.HEALTHY: 300,
    FleetHealthState.BUSY: 180,
    FleetHealthState.DEGRADED: 80,
    FleetHealthState.STALE: -1000,
    FleetHealthState.OFFLINE: -1000,
    FleetHealthState.INCOMPATIBLE: -1000,
    FleetHealthState.QUARANTINED: -1000,
}

_TRUST_SCORE = {
    FleetTrustTier.UNTRUSTED: 0,
    FleetTrustTier.OBSERVED: 25,
    FleetTrustTier.REVIEWED: 50,
    FleetTrustTier.SANDBOXED: 75,
    FleetTrustTier.CERTIFIED: 100,
}

_TRUST_ORDER = {
    FleetTrustTier.UNTRUSTED: 0,
    FleetTrustTier.OBSERVED: 1,
    FleetTrustTier.REVIEWED: 2,
    FleetTrustTier.SANDBOXED: 3,
    FleetTrustTier.CERTIFIED: 4,
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise FleetRoutingValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise FleetRoutingValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise FleetRoutingValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise FleetRoutingValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise FleetRoutingValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise FleetRoutingValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _exact_decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise FleetRoutingValidationError(
            f"{label} must be an exact decimal"
        ) from error

    if not parsed.is_finite():
        raise FleetRoutingValidationError(f"{label} must be finite")

    return parsed


@dataclass(frozen=True, slots=True)
class FleetRoutingConfig:
    enabled: bool = False
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    minimum_trust_tier: FleetTrustTier = FleetTrustTier.REVIEWED
    maximum_fallbacks: int = 2
    schema_version: int = FLEET_ROUTING_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != FLEET_ROUTING_SCHEMA_VERSION:
            raise FleetRoutingValidationError(
                "unsupported fleet routing schema"
            )
        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise FleetRoutingValidationError(
                "incompatible worker contract schema"
            )
        if not isinstance(self.minimum_trust_tier, FleetTrustTier):
            raise FleetRoutingValidationError(
                "unknown minimum fleet trust tier"
            )
        if not 0 <= self.maximum_fallbacks <= 10:
            raise FleetRoutingValidationError(
                "fallback count is outside bounds"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "fleet routing configuration")

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_provision(self) -> bool:
        return False

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_ssh(self) -> bool:
        return False

    @property
    def can_execute_shell(self) -> bool:
        return False

    @property
    def can_use_credentials(self) -> bool:
        return False

    @property
    def can_activate_integration(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FleetCapacity:
    total_job_slots: int
    available_job_slots: int
    total_memory_megabytes: int
    available_memory_megabytes: int
    total_compute_units: int
    available_compute_units: int

    def __post_init__(self) -> None:
        if not 1 <= self.total_job_slots <= 10000:
            raise FleetRoutingValidationError(
                "total job slots are outside bounds"
            )
        if not 0 <= self.available_job_slots <= self.total_job_slots:
            raise FleetRoutingValidationError(
                "available job slots are inconsistent"
            )
        if not 256 <= self.total_memory_megabytes <= 4_194_304:
            raise FleetRoutingValidationError(
                "total memory is outside bounds"
            )
        if not 0 <= self.available_memory_megabytes <= (
            self.total_memory_megabytes
        ):
            raise FleetRoutingValidationError(
                "available memory is inconsistent"
            )
        if not 1 <= self.total_compute_units <= 10_000_000:
            raise FleetRoutingValidationError(
                "total compute units are outside bounds"
            )
        if not 0 <= self.available_compute_units <= (
            self.total_compute_units
        ):
            raise FleetRoutingValidationError(
                "available compute units are inconsistent"
            )


@dataclass(frozen=True, slots=True)
class FleetNode:
    node_id: str
    machine_id: str
    display_name: str
    role: FleetNodeRole
    priority: int
    trust_tier: FleetTrustTier
    worker_contract_schema: int
    capabilities: tuple[str, ...]
    supported_machines: tuple[str, ...]
    supported_profiles: tuple[str, ...]
    cost_per_hour_usd: str
    enabled: bool
    node_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.node_digest and self.node_digest != expected:
            raise FleetRoutingValidationError(
                "fleet node digest mismatch"
            )
        if not self.node_digest:
            object.__setattr__(self, "node_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["role"] = self.role.value
        payload["trust_tier"] = self.trust_tier.value
        payload.pop("node_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        _require_identifier(self.node_id, "fleet node ID")
        _require_identifier(self.machine_id, "machine ID")

        if not self.display_name.strip():
            raise FleetRoutingValidationError(
                "fleet node display name is required"
            )
        if not isinstance(self.role, FleetNodeRole):
            raise FleetRoutingValidationError(
                "unknown fleet node role"
            )
        if not isinstance(self.trust_tier, FleetTrustTier):
            raise FleetRoutingValidationError(
                "unknown fleet trust tier"
            )
        if not 0 <= self.priority <= 1000:
            raise FleetRoutingValidationError(
                "fleet node priority is outside bounds"
            )
        if self.worker_contract_schema < 1:
            raise FleetRoutingValidationError(
                "fleet worker schema must be positive"
            )

        cost = _exact_decimal(
            self.cost_per_hour_usd,
            "fleet hourly cost",
        )
        if cost < Decimal("0") or cost > Decimal("1000000"):
            raise FleetRoutingValidationError(
                "fleet hourly cost is outside bounds"
            )

        for values, label in (
            (self.capabilities, "fleet capability"),
            (self.supported_machines, "supported machine"),
            (self.supported_profiles, "supported profile"),
        ):
            for value in values:
                _require_identifier(value, label)
            if len(set(values)) != len(values):
                raise FleetRoutingValidationError(
                    f"duplicate {label}"
                )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "fleet node",
        )

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_execute(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FleetEvidence:
    node_id: str
    observed_at: str
    health: FleetHealthState
    lease_state: FleetLeaseState
    capacity: FleetCapacity
    running_jobs: int
    recent_failures: int
    latency_milliseconds: int
    evidence_digest: str
    sanitized_summary: str

    def __post_init__(self) -> None:
        _require_identifier(self.node_id, "evidence node ID")
        _require_timestamp(self.observed_at, "fleet evidence time")
        _require_digest(self.evidence_digest, "fleet evidence digest")

        if not isinstance(self.health, FleetHealthState):
            raise FleetRoutingValidationError(
                "unknown fleet health state"
            )
        if not isinstance(self.lease_state, FleetLeaseState):
            raise FleetRoutingValidationError(
                "unknown fleet lease state"
            )
        if not 0 <= self.running_jobs <= 10000:
            raise FleetRoutingValidationError(
                "running job count is outside bounds"
            )
        if not 0 <= self.recent_failures <= 10000:
            raise FleetRoutingValidationError(
                "recent failure count is outside bounds"
            )
        if not 0 <= self.latency_milliseconds <= 3_600_000:
            raise FleetRoutingValidationError(
                "fleet latency is outside bounds"
            )
        if not self.sanitized_summary.strip():
            raise FleetRoutingValidationError(
                "fleet evidence summary is required"
            )

        _validate_sanitized(asdict(self), "fleet evidence")


@dataclass(frozen=True, slots=True)
class RoutingRequirements:
    minimum_memory_megabytes: int
    minimum_compute_units: int
    required_evidence_kinds: tuple[str, ...]
    maximum_hourly_cost_usd: str

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_memory_megabytes <= 4_194_304:
            raise FleetRoutingValidationError(
                "minimum memory is outside bounds"
            )
        if not 0 <= self.minimum_compute_units <= 10_000_000:
            raise FleetRoutingValidationError(
                "minimum compute units are outside bounds"
            )

        maximum_cost = _exact_decimal(
            self.maximum_hourly_cost_usd,
            "maximum routing cost",
        )
        if maximum_cost < Decimal("0"):
            raise FleetRoutingValidationError(
                "maximum routing cost cannot be negative"
            )

        for kind in self.required_evidence_kinds:
            _require_identifier(kind, "required evidence kind")

        if len(set(self.required_evidence_kinds)) != len(
            self.required_evidence_kinds
        ):
            raise FleetRoutingValidationError(
                "duplicate required evidence kind"
            )


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    node_id: str
    eligibility: RouteEligibility
    eligible: bool
    score: int
    exclusion_reasons: tuple[str, ...]
    capability_match: bool
    machine_match: bool
    profile_match: bool
    health_current: bool
    lease_available: bool
    capacity_available: bool
    budget_available: bool
    trust_sufficient: bool
    worker_contract_compatible: bool
    node_digest: str
    evidence_digest: str

    def __post_init__(self) -> None:
        _require_identifier(self.node_id, "candidate node ID")
        _require_digest(self.node_digest, "candidate node digest")
        _require_digest(
            self.evidence_digest,
            "candidate evidence digest",
        )

        if not isinstance(self.eligibility, RouteEligibility):
            raise FleetRoutingValidationError(
                "unknown route eligibility"
            )
        if self.eligible != (
            self.eligibility is RouteEligibility.ELIGIBLE
        ):
            raise FleetRoutingValidationError(
                "route candidate eligibility is inconsistent"
            )
        if self.eligible and self.exclusion_reasons:
            raise FleetRoutingValidationError(
                "eligible route cannot have exclusion reasons"
            )
        if not self.eligible and not self.exclusion_reasons:
            raise FleetRoutingValidationError(
                "excluded route requires reasons"
            )

        _validate_sanitized(asdict(self), "route candidate")

    @property
    def can_dispatch(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FleetRouteDecision:
    job_id: str
    primary_node_id: str | None
    fallback_node_ids: tuple[str, ...]
    candidates: tuple[RouteCandidate, ...]
    decision_reason: str
    worker_contract_digest: str
    schema_version: int = FLEET_ROUTING_SCHEMA_VERSION
    decision_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.decision_digest and self.decision_digest != expected:
            raise FleetRoutingValidationError(
                "fleet route decision digest mismatch"
            )
        if not self.decision_digest:
            object.__setattr__(self, "decision_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("decision_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != FLEET_ROUTING_SCHEMA_VERSION:
            raise FleetRoutingValidationError(
                "unsupported fleet route decision schema"
            )

        _require_identifier(self.job_id, "decision job ID")
        _require_digest(
            self.worker_contract_digest,
            "worker contract digest",
        )

        if self.primary_node_id is not None:
            _require_identifier(
                self.primary_node_id,
                "primary node ID",
            )

        for node_id in self.fallback_node_ids:
            _require_identifier(node_id, "fallback node ID")

        if len(set(self.fallback_node_ids)) != len(
            self.fallback_node_ids
        ):
            raise FleetRoutingValidationError(
                "duplicate fallback node identity"
            )
        if self.primary_node_id in self.fallback_node_ids:
            raise FleetRoutingValidationError(
                "primary node cannot also be a fallback"
            )
        if len({item.node_id for item in self.candidates}) != len(
            self.candidates
        ):
            raise FleetRoutingValidationError(
                "duplicate route candidate identity"
            )
        if not self.decision_reason.strip():
            raise FleetRoutingValidationError(
                "routing decision reason is required"
            )

        eligible_ids = {
            item.node_id
            for item in self.candidates
            if item.eligible
        }

        if self.primary_node_id is not None:
            if self.primary_node_id not in eligible_ids:
                raise FleetRoutingValidationError(
                    "primary route must be eligible"
                )
        elif eligible_ids:
            raise FleetRoutingValidationError(
                "eligible candidates require a primary route"
            )

        if not set(self.fallback_node_ids).issubset(eligible_ids):
            raise FleetRoutingValidationError(
                "fallback routes must be eligible"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "fleet route decision",
        )

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_failover(self) -> bool:
        return False


def evaluate_candidate(
    config: FleetRoutingConfig,
    job: GovernedWorkerJob,
    node: FleetNode,
    evidence: FleetEvidence,
    requirements: RoutingRequirements,
    *,
    evidence_age_seconds: int,
    stale_after_seconds: int = 120,
) -> RouteCandidate:
    """Evaluate one injected node without dispatching work."""

    if not 1 <= stale_after_seconds <= 86400:
        raise FleetRoutingValidationError(
            "fleet staleness threshold is outside bounds"
        )
    if evidence_age_seconds < 0:
        raise FleetRoutingValidationError(
            "fleet evidence cannot originate in the future"
        )
    if evidence.node_id != node.node_id:
        raise FleetRoutingValidationError(
            "fleet evidence does not match node identity"
        )

    capability_match = job.requested_capability in node.capabilities
    machine_match = job.target_machine in node.supported_machines
    profile_match = job.target_profile in node.supported_profiles
    health_current = (
        evidence_age_seconds <= stale_after_seconds
        and evidence.health
        in {
            FleetHealthState.HEALTHY,
            FleetHealthState.BUSY,
            FleetHealthState.DEGRADED,
        }
    )
    lease_available = evidence.lease_state in {
        FleetLeaseState.AVAILABLE,
        FleetLeaseState.RESERVED,
        FleetLeaseState.ACTIVE,
        FleetLeaseState.EXPIRING,
    }
    capacity_available = (
        evidence.capacity.available_job_slots > 0
        and evidence.capacity.available_memory_megabytes
        >= requirements.minimum_memory_megabytes
        and evidence.capacity.available_compute_units
        >= requirements.minimum_compute_units
    )
    node_cost = _exact_decimal(
        node.cost_per_hour_usd,
        "fleet hourly cost",
    )
    maximum_cost = _exact_decimal(
        requirements.maximum_hourly_cost_usd,
        "maximum routing cost",
    )
    budget_available = node_cost <= maximum_cost
    trust_sufficient = (
        _TRUST_ORDER[node.trust_tier]
        >= _TRUST_ORDER[config.minimum_trust_tier]
    )
    compatible = (
        node.worker_contract_schema
        == config.expected_worker_contract_schema
        == job.schema_version
    )

    reasons: list[str] = []

    if not config.enabled:
        eligibility = RouteEligibility.DISABLED
        reasons.append("Fleet routing is disabled by policy.")
    elif not node.enabled:
        eligibility = RouteEligibility.DISABLED
        reasons.append("Fleet node is disabled.")
    elif not compatible:
        eligibility = RouteEligibility.SCHEMA_INCOMPATIBLE
        reasons.append("Worker contract schema is incompatible.")
    elif not trust_sufficient:
        eligibility = RouteEligibility.TRUST_BLOCKED
        reasons.append("Fleet trust tier is below the governed minimum.")
    elif not capability_match:
        eligibility = RouteEligibility.CAPABILITY_MISMATCH
        reasons.append("Required capability is unavailable.")
    elif not machine_match:
        eligibility = RouteEligibility.MACHINE_MISMATCH
        reasons.append("Target machine is unsupported.")
    elif not profile_match:
        eligibility = RouteEligibility.PROFILE_MISMATCH
        reasons.append("Target profile is unsupported.")
    elif evidence_age_seconds > stale_after_seconds:
        eligibility = RouteEligibility.STALE_EVIDENCE
        reasons.append("Fleet evidence exceeded the freshness window.")
    elif evidence.health not in {
        FleetHealthState.HEALTHY,
        FleetHealthState.BUSY,
        FleetHealthState.DEGRADED,
    }:
        eligibility = RouteEligibility.HEALTH_BLOCKED
        reasons.append(
            f"Fleet health state {evidence.health.value} blocks routing."
        )
    elif not lease_available:
        eligibility = RouteEligibility.LEASE_BLOCKED
        reasons.append(
            f"Fleet lease state {evidence.lease_state.value} blocks routing."
        )
    elif not capacity_available:
        eligibility = RouteEligibility.CAPACITY_BLOCKED
        reasons.append("Governed fleet capacity is insufficient.")
    elif not budget_available:
        eligibility = RouteEligibility.BUDGET_BLOCKED
        reasons.append("Fleet node exceeds the hourly cost budget.")
    else:
        eligibility = RouteEligibility.ELIGIBLE

    eligible = eligibility is RouteEligibility.ELIGIBLE

    score = -1_000_000

    if eligible:
        utilization_penalty = (
            evidence.running_jobs * 15
            + evidence.recent_failures * 25
            + evidence.latency_milliseconds // 100
        )
        capacity_bonus = min(
            evidence.capacity.available_job_slots * 10,
            100,
        )
        score = (
            _ROLE_SCORE[node.role]
            + _HEALTH_SCORE[evidence.health]
            + _TRUST_SCORE[node.trust_tier]
            + node.priority
            + capacity_bonus
            - utilization_penalty
        )

    return RouteCandidate(
        node_id=node.node_id,
        eligibility=eligibility,
        eligible=eligible,
        score=score,
        exclusion_reasons=tuple(reasons),
        capability_match=capability_match,
        machine_match=machine_match,
        profile_match=profile_match,
        health_current=health_current,
        lease_available=lease_available,
        capacity_available=capacity_available,
        budget_available=budget_available,
        trust_sufficient=trust_sufficient,
        worker_contract_compatible=compatible,
        node_digest=node.node_digest,
        evidence_digest=evidence.evidence_digest,
    )


def route_worker_job(
    config: FleetRoutingConfig,
    job: GovernedWorkerJob,
    nodes: tuple[FleetNode, ...],
    evidence_by_node: Mapping[str, FleetEvidence],
    requirements: RoutingRequirements,
    *,
    evidence_age_seconds_by_node: Mapping[str, int],
    stale_after_seconds: int = 120,
) -> FleetRouteDecision:
    """Select deterministic primary and fallback projections."""

    job.authority.validate()

    if len({node.node_id for node in nodes}) != len(nodes):
        raise FleetRoutingValidationError(
            "duplicate fleet node identity"
        )

    candidates: list[RouteCandidate] = []

    for node in nodes:
        evidence = evidence_by_node.get(node.node_id)
        age = evidence_age_seconds_by_node.get(node.node_id)

        if evidence is None or age is None:
            raise FleetRoutingValidationError(
                f"missing fleet evidence for {node.node_id}"
            )

        candidates.append(
            evaluate_candidate(
                config,
                job,
                node,
                evidence,
                requirements,
                evidence_age_seconds=age,
                stale_after_seconds=stale_after_seconds,
            )
        )

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            candidate.node_id,
        ),
    )
    eligible = [item for item in ordered if item.eligible]

    primary = eligible[0].node_id if eligible else None
    fallbacks = tuple(
        item.node_id
        for item in eligible[1 : 1 + config.maximum_fallbacks]
    )

    if primary is None:
        reason = (
            "No governed fleet node is eligible for the worker job. "
            "All exclusion reasons are preserved in the candidate records."
        )
    elif fallbacks:
        reason = (
            f"{primary} is the deterministic primary route; "
            f"{', '.join(fallbacks)} are ordered fallbacks."
        )
    else:
        reason = (
            f"{primary} is the only governed fleet node eligible "
            "for this worker job."
        )

    return FleetRouteDecision(
        job_id=job.job_id,
        primary_node_id=primary,
        fallback_node_ids=fallbacks,
        candidates=tuple(ordered),
        decision_reason=reason,
        worker_contract_digest=job.contract_digest,
    )
