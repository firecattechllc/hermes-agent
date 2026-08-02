"""Disabled-by-default governed Agent Reach adapter.

Stage 8A models externally reachable agent identities, trust tiers, allowlisted
capabilities, route references, request/response envelopes, rate and budget
limits, heartbeat evidence, compatibility, and worker lifecycle projection.

This module performs no network requests, outreach, authentication, credential
exchange, arbitrary messaging, job dispatch, execution, approval, filesystem
access, shell execution, installation, activation, or financial action.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.worker_contract import (
    WORKER_CONTRACT_SCHEMA_VERSION,
    GovernedWorkerJob,
    JobState,
)

AGENT_REACH_ADAPTER_SCHEMA_VERSION = 1

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
    r"(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
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


class AgentReachValidationError(ValueError):
    """Agent Reach adapter input failed closed."""


class AgentTrustTier(str, Enum):
    UNTRUSTED = "untrusted"
    OBSERVED = "observed"
    REVIEWED = "reviewed"
    SANDBOXED = "sandboxed"
    CERTIFIED = "certified"


class AgentReachState(str, Enum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    STALE = "stale"
    OFFLINE = "offline"
    INCOMPATIBLE = "incompatible"
    RATE_BLOCKED = "rate_blocked"
    BUDGET_BLOCKED = "budget_blocked"
    CAPABILITY_BLOCKED = "capability_blocked"
    TRUST_BLOCKED = "trust_blocked"


class ReachResponseState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    REJECTED = "rejected"
    COMPLETION_UNKNOWN = "completion_unknown"


class AgentReachWorkState(str, Enum):
    PROPOSED = "proposed"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPLETION_UNKNOWN = "completion_unknown"


_JOB_STATE_PROJECTION: dict[JobState, AgentReachWorkState] = {
    JobState.PROPOSED: AgentReachWorkState.PROPOSED,
    JobState.ADMITTED: AgentReachWorkState.ADMITTED,
    JobState.REJECTED: AgentReachWorkState.REJECTED,
    JobState.QUEUED: AgentReachWorkState.QUEUED,
    JobState.RUNNING: AgentReachWorkState.RUNNING,
    JobState.CANCELLATION_REQUESTED: (
        AgentReachWorkState.CANCELLATION_REQUESTED
    ),
    JobState.CANCELLED: AgentReachWorkState.CANCELLED,
    JobState.SUCCEEDED: AgentReachWorkState.SUCCEEDED,
    JobState.FAILED: AgentReachWorkState.FAILED,
    JobState.COMPLETION_UNKNOWN: (
        AgentReachWorkState.COMPLETION_UNKNOWN
    ),
}

_TRUST_ORDER = {
    AgentTrustTier.UNTRUSTED: 0,
    AgentTrustTier.OBSERVED: 1,
    AgentTrustTier.REVIEWED: 2,
    AgentTrustTier.SANDBOXED: 3,
    AgentTrustTier.CERTIFIED: 4,
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise AgentReachValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise AgentReachValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise AgentReachValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise AgentReachValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise AgentReachValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise AgentReachValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise AgentReachValidationError(
            f"{label} must be a repository-relative reference"
        )


def _exact_decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise AgentReachValidationError(
            f"{label} must be an exact decimal"
        ) from error

    if not parsed.is_finite():
        raise AgentReachValidationError(f"{label} must be finite")

    return parsed


@dataclass(frozen=True, slots=True)
class AgentReachConfig:
    integration_id: str = "agent-reach"
    enabled: bool = False
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    minimum_trust_tier: AgentTrustTier = AgentTrustTier.REVIEWED
    schema_version: int = AGENT_REACH_ADAPTER_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_REACH_ADAPTER_SCHEMA_VERSION:
            raise AgentReachValidationError(
                "unsupported Agent Reach adapter schema"
            )

        _require_identifier(
            self.integration_id,
            "Agent Reach integration ID",
        )

        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise AgentReachValidationError(
                "incompatible worker contract schema"
            )
        if not isinstance(self.minimum_trust_tier, AgentTrustTier):
            raise AgentReachValidationError(
                "unknown minimum trust tier"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Agent Reach configuration")

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_exchange_credentials(self) -> bool:
        return False

    @property
    def can_send_message(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_execute(self) -> bool:
        return False

    @property
    def can_approve(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ReachAgentIdentity:
    agent_id: str
    organization_identity: str
    display_name: str
    trust_tier: AgentTrustTier
    worker_contract_schema: int
    capabilities: tuple[str, ...]
    supported_machines: tuple[str, ...]
    supported_profiles: tuple[str, ...]
    identity_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.identity_digest and self.identity_digest != expected:
            raise AgentReachValidationError(
                "Agent Reach identity digest mismatch"
            )
        if not self.identity_digest:
            object.__setattr__(self, "identity_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["trust_tier"] = self.trust_tier.value
        payload.pop("identity_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        _require_identifier(self.agent_id, "external agent ID")

        if not self.organization_identity.strip():
            raise AgentReachValidationError(
                "agent organization identity is required"
            )
        if not self.display_name.strip():
            raise AgentReachValidationError(
                "agent display name is required"
            )
        if not isinstance(self.trust_tier, AgentTrustTier):
            raise AgentReachValidationError(
                "unknown agent trust tier"
            )
        if self.worker_contract_schema < 1:
            raise AgentReachValidationError(
                "worker contract schema must be positive"
            )

        for values, label in (
            (self.capabilities, "agent capability"),
            (self.supported_machines, "supported machine"),
            (self.supported_profiles, "supported profile"),
        ):
            for value in values:
                _require_identifier(value, label)
            if len(set(values)) != len(values):
                raise AgentReachValidationError(
                    f"duplicate {label}"
                )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Agent Reach identity",
        )

    @property
    def can_execute(self) -> bool:
        return False

    @property
    def can_approve(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ReachRouteRef:
    route_id: str
    transport_classification: str
    route_reference: str
    one_way: bool
    authenticated: bool = False
    credential_exchange_required: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.route_id, "route ID")
        _require_identifier(
            self.transport_classification,
            "transport classification",
        )

        _validate_sanitized(asdict(self), "Agent Reach route")
        _require_relative_reference(
            self.route_reference,
            "route reference",
        )

        if self.authenticated:
            raise AgentReachValidationError(
                "live authentication is prohibited in Stage 8A"
            )
        if self.credential_exchange_required:
            raise AgentReachValidationError(
                "credential exchange is prohibited in Stage 8A"
            )


@dataclass(frozen=True, slots=True)
class ReachLimits:
    maximum_requests_per_hour: int
    maximum_in_flight_requests: int
    maximum_request_bytes: int
    maximum_response_bytes: int
    maximum_runtime_seconds: int
    maximum_cost_usd: str

    def __post_init__(self) -> None:
        cost = _exact_decimal(
            self.maximum_cost_usd,
            "maximum reach cost",
        )

        if not 1 <= self.maximum_requests_per_hour <= 10000:
            raise AgentReachValidationError(
                "request rate limit is outside bounds"
            )
        if not 1 <= self.maximum_in_flight_requests <= 100:
            raise AgentReachValidationError(
                "in-flight request limit is outside bounds"
            )
        if not 1 <= self.maximum_request_bytes <= 100_000_000:
            raise AgentReachValidationError(
                "request byte limit is outside bounds"
            )
        if not 1 <= self.maximum_response_bytes <= 100_000_000:
            raise AgentReachValidationError(
                "response byte limit is outside bounds"
            )
        if not 1 <= self.maximum_runtime_seconds <= 86400:
            raise AgentReachValidationError(
                "runtime limit is outside bounds"
            )
        if cost < Decimal("0") or cost > Decimal("1000000"):
            raise AgentReachValidationError(
                "maximum reach cost is outside bounds"
            )


@dataclass(frozen=True, slots=True)
class ReachEvidenceRequirement:
    minimum_references: int
    required_kinds: tuple[str, ...]
    require_content_digests: bool
    require_provenance: bool

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_references <= 1000:
            raise AgentReachValidationError(
                "minimum evidence references are outside bounds"
            )

        for kind in self.required_kinds:
            _require_identifier(kind, "evidence kind")

        if len(set(self.required_kinds)) != len(self.required_kinds):
            raise AgentReachValidationError(
                "duplicate evidence kind"
            )


@dataclass(frozen=True, slots=True)
class ReachEvidenceRef:
    evidence_id: str
    kind: str
    content_digest: str
    provenance: str
    reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence ID")
        _require_identifier(self.kind, "evidence kind")
        _require_digest(self.content_digest, "evidence content digest")

        if not self.provenance.strip():
            raise AgentReachValidationError(
                "evidence provenance is required"
            )

        _validate_sanitized(asdict(self), "Agent Reach evidence")
        _require_relative_reference(
            self.reference,
            "evidence reference",
        )


@dataclass(frozen=True, slots=True)
class ReachRequestEnvelope:
    request_id: str
    correlation_id: str
    idempotency_key: str
    requesting_actor_identity: str
    target_agent_id: str
    requested_capability: str
    target_machine: str
    target_profile: str
    created_at: str
    deadline_at: str
    payload: Mapping[str, object]
    payload_digest: str
    limits: ReachLimits
    evidence_requirements: ReachEvidenceRequirement
    schema_version: int = AGENT_REACH_ADAPTER_SCHEMA_VERSION
    request_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.request_digest and self.request_digest != expected:
            raise AgentReachValidationError(
                "Agent Reach request digest mismatch"
            )
        if not self.request_digest:
            object.__setattr__(self, "request_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("request_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != AGENT_REACH_ADAPTER_SCHEMA_VERSION:
            raise AgentReachValidationError(
                "unsupported Agent Reach request schema"
            )

        for value, label in (
            (self.request_id, "request ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
            (self.target_agent_id, "target agent ID"),
            (self.requested_capability, "requested capability"),
            (self.target_machine, "target machine"),
            (self.target_profile, "target profile"),
        ):
            _require_identifier(value, label)

        if not self.requesting_actor_identity.strip():
            raise AgentReachValidationError(
                "requesting actor identity is required"
            )

        _require_timestamp(self.created_at, "request creation time")
        _require_timestamp(self.deadline_at, "request deadline")
        _require_digest(self.payload_digest, "request payload digest")

        expected_payload_digest = (
            f"sha256:{canonical_digest(dict(self.payload))}"
        )
        if self.payload_digest != expected_payload_digest:
            raise AgentReachValidationError(
                "Agent Reach request payload digest mismatch"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Agent Reach request",
        )

    @property
    def can_send(self) -> bool:
        return False

    @property
    def can_execute(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ReachResponseEnvelope:
    response_id: str
    request_id: str
    correlation_id: str
    responding_agent_id: str
    state: ReachResponseState
    completed_at: str
    output_payload: Mapping[str, object]
    output_digest: str
    evidence: tuple[ReachEvidenceRef, ...]
    runtime_seconds: int
    response_bytes: int
    cost_usd: str
    schema_version: int = AGENT_REACH_ADAPTER_SCHEMA_VERSION
    response_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.response_digest and self.response_digest != expected:
            raise AgentReachValidationError(
                "Agent Reach response digest mismatch"
            )
        if not self.response_digest:
            object.__setattr__(self, "response_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload.pop("response_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != AGENT_REACH_ADAPTER_SCHEMA_VERSION:
            raise AgentReachValidationError(
                "unsupported Agent Reach response schema"
            )

        for value, label in (
            (self.response_id, "response ID"),
            (self.request_id, "request ID"),
            (self.correlation_id, "correlation ID"),
            (self.responding_agent_id, "responding agent ID"),
        ):
            _require_identifier(value, label)

        if not isinstance(self.state, ReachResponseState):
            raise AgentReachValidationError(
                "unknown response state"
            )

        _require_timestamp(self.completed_at, "response completion time")
        _require_digest(self.output_digest, "response output digest")

        expected_output_digest = (
            f"sha256:{canonical_digest(dict(self.output_payload))}"
        )
        if self.output_digest != expected_output_digest:
            raise AgentReachValidationError(
                "Agent Reach response output digest mismatch"
            )

        if not 0 <= self.runtime_seconds <= 86400:
            raise AgentReachValidationError(
                "response runtime is outside bounds"
            )
        if not 0 <= self.response_bytes <= 100_000_000:
            raise AgentReachValidationError(
                "response byte count is outside bounds"
            )

        cost = _exact_decimal(self.cost_usd, "response cost")

        if cost < Decimal("0"):
            raise AgentReachValidationError(
                "response cost cannot be negative"
            )

        if len({item.evidence_id for item in self.evidence}) != len(
            self.evidence
        ):
            raise AgentReachValidationError(
                "duplicate response evidence identity"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Agent Reach response",
        )

    def validate_for(
        self,
        request: ReachRequestEnvelope,
        identity: ReachAgentIdentity,
    ) -> None:
        if self.request_id != request.request_id:
            raise AgentReachValidationError(
                "response does not match request identity"
            )
        if self.correlation_id != request.correlation_id:
            raise AgentReachValidationError(
                "response correlation does not match request"
            )
        if self.responding_agent_id != identity.agent_id:
            raise AgentReachValidationError(
                "response agent does not match Agent Reach identity"
            )
        if request.target_agent_id != identity.agent_id:
            raise AgentReachValidationError(
                "request target does not match Agent Reach identity"
            )

        required_kinds = set(
            request.evidence_requirements.required_kinds
        )
        provided_kinds = {item.kind for item in self.evidence}

        if (
            len(self.evidence)
            < request.evidence_requirements.minimum_references
        ):
            raise AgentReachValidationError(
                "response has insufficient evidence references"
            )
        if not required_kinds.issubset(provided_kinds):
            raise AgentReachValidationError(
                "response is missing required evidence kinds"
            )

        if self.runtime_seconds > request.limits.maximum_runtime_seconds:
            raise AgentReachValidationError(
                "response runtime exceeds request limits"
            )
        if self.response_bytes > request.limits.maximum_response_bytes:
            raise AgentReachValidationError(
                "response bytes exceed request limits"
            )

        cost = _exact_decimal(self.cost_usd, "response cost")
        maximum = _exact_decimal(
            request.limits.maximum_cost_usd,
            "maximum request cost",
        )

        if cost > maximum:
            raise AgentReachValidationError(
                "response cost exceeds request budget"
            )


@dataclass(frozen=True, slots=True)
class ReachHeartbeat:
    agent_id: str
    observed_at: str
    sequence: int
    online: bool
    worker_contract_schema: int
    active_requests: int
    requests_last_hour: int
    sanitized_summary: str

    def __post_init__(self) -> None:
        _require_identifier(self.agent_id, "heartbeat agent ID")
        _require_timestamp(self.observed_at, "heartbeat observation time")

        if self.sequence < 0:
            raise AgentReachValidationError(
                "heartbeat sequence cannot be negative"
            )
        if self.worker_contract_schema < 1:
            raise AgentReachValidationError(
                "heartbeat worker schema must be positive"
            )
        if self.active_requests < 0:
            raise AgentReachValidationError(
                "active request count cannot be negative"
            )
        if self.requests_last_hour < 0:
            raise AgentReachValidationError(
                "hourly request count cannot be negative"
            )
        if not self.sanitized_summary.strip():
            raise AgentReachValidationError(
                "heartbeat summary is required"
            )

        _validate_sanitized(asdict(self), "Agent Reach heartbeat")


@dataclass(frozen=True, slots=True)
class AgentReachAssessment:
    agent_id: str
    state: AgentReachState
    enabled: bool
    trust_sufficient: bool
    worker_contract_compatible: bool
    heartbeat_current: bool
    capability_allowed: bool
    machine_supported: bool
    profile_supported: bool
    rate_available: bool
    budget_available: bool
    reason: str
    identity_digest: str
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.agent_id, "assessment agent ID")
        _require_digest(self.identity_digest, "agent identity digest")

        if not isinstance(self.state, AgentReachState):
            raise AgentReachValidationError(
                "unknown Agent Reach state"
            )
        if not self.reason.strip():
            raise AgentReachValidationError(
                "Agent Reach assessment reason is required"
            )

        self.authority.validate()
        _validate_sanitized(
            asdict(self),
            "Agent Reach assessment",
        )

    @property
    def can_reach(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class AgentReachJobProjection:
    job_id: str
    correlation_id: str
    idempotency_key: str
    requested_capability: str
    target_machine: str
    target_profile: str
    state: AgentReachWorkState
    worker_contract_digest: str
    worker_contract_schema: int
    created_at: str
    schema_version: int = AGENT_REACH_ADAPTER_SCHEMA_VERSION
    projection_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        for value, label in (
            (self.job_id, "job ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
            (self.requested_capability, "requested capability"),
            (self.target_machine, "target machine"),
            (self.target_profile, "target profile"),
        ):
            _require_identifier(value, label)

        if not isinstance(self.state, AgentReachWorkState):
            raise AgentReachValidationError(
                "unknown Agent Reach work state"
            )

        _require_digest(
            self.worker_contract_digest,
            "worker contract digest",
        )
        _require_timestamp(self.created_at, "job creation time")

        if self.worker_contract_schema != WORKER_CONTRACT_SCHEMA_VERSION:
            raise AgentReachValidationError(
                "Agent Reach job projection worker schema is incompatible"
            )
        if self.schema_version != AGENT_REACH_ADAPTER_SCHEMA_VERSION:
            raise AgentReachValidationError(
                "unsupported Agent Reach job projection schema"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Agent Reach job projection",
        )

        expected = self.expected_digest()

        if self.projection_digest and self.projection_digest != expected:
            raise AgentReachValidationError(
                "Agent Reach job projection digest mismatch"
            )
        if not self.projection_digest:
            object.__setattr__(self, "projection_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload.pop("projection_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"


def validate_agent_reach_registry_entry(
    config: AgentReachConfig,
    entry: IntegrationRegistryEntry,
) -> None:
    """Validate that Stage 1 describes Agent Reach safely."""

    if entry.integration_id != config.integration_id:
        raise AgentReachValidationError(
            "Agent Reach registry identity mismatch"
        )
    if entry.category is not IntegrationCategory.INTERNET_CAPABILITY:
        raise AgentReachValidationError(
            "Agent Reach must be registered as an internet capability"
        )
    if entry.lifecycle_state in {
        LifecycleState.REJECTED,
        LifecycleState.DEPRECATED,
        LifecycleState.QUARANTINED,
    }:
        raise AgentReachValidationError(
            "Agent Reach registry lifecycle is not eligible"
        )

    entry.authority.validate()

    if entry.can_activate:
        raise AgentReachValidationError(
            "Agent Reach registry entry unexpectedly permits activation"
        )


def evaluate_agent_reach(
    config: AgentReachConfig,
    identity: ReachAgentIdentity,
    request: ReachRequestEnvelope,
    heartbeat: ReachHeartbeat | None,
    *,
    heartbeat_age_seconds: int | None,
    current_hourly_requests: int,
    current_in_flight_requests: int,
    current_cost_usd: str,
    stale_after_seconds: int = 120,
) -> AgentReachAssessment:
    """Evaluate injected reachability evidence without contacting an agent."""

    if not 1 <= stale_after_seconds <= 86400:
        raise AgentReachValidationError(
            "heartbeat staleness threshold is outside bounds"
        )
    if current_hourly_requests < 0:
        raise AgentReachValidationError(
            "current hourly request count cannot be negative"
        )
    if current_in_flight_requests < 0:
        raise AgentReachValidationError(
            "current in-flight request count cannot be negative"
        )

    current_cost = _exact_decimal(
        current_cost_usd,
        "current reach cost",
    )
    maximum_cost = _exact_decimal(
        request.limits.maximum_cost_usd,
        "maximum reach cost",
    )

    if current_cost < Decimal("0"):
        raise AgentReachValidationError(
            "current reach cost cannot be negative"
        )

    trust_sufficient = (
        _TRUST_ORDER[identity.trust_tier]
        >= _TRUST_ORDER[config.minimum_trust_tier]
    )
    compatible = (
        identity.worker_contract_schema
        == config.expected_worker_contract_schema
    )
    capability_allowed = (
        request.requested_capability in identity.capabilities
    )
    machine_supported = (
        request.target_machine in identity.supported_machines
    )
    profile_supported = (
        request.target_profile in identity.supported_profiles
    )
    rate_available = (
        current_hourly_requests
        < request.limits.maximum_requests_per_hour
        and current_in_flight_requests
        < request.limits.maximum_in_flight_requests
    )
    budget_available = current_cost < maximum_cost

    if request.target_agent_id != identity.agent_id:
        raise AgentReachValidationError(
            "request target does not match Agent Reach identity"
        )

    if not config.enabled:
        state = AgentReachState.DISABLED
        reason = "Agent Reach adapter is disabled by policy."
        heartbeat_current = False
    elif not trust_sufficient:
        state = AgentReachState.TRUST_BLOCKED
        reason = "Agent trust tier is below the governed minimum."
        heartbeat_current = False
    elif not compatible:
        state = AgentReachState.INCOMPATIBLE
        reason = "Agent worker contract schema is incompatible."
        heartbeat_current = False
    elif not capability_allowed or not machine_supported or not profile_supported:
        state = AgentReachState.CAPABILITY_BLOCKED
        reason = "Requested capability or target is not allowlisted."
        heartbeat_current = False
    elif not rate_available:
        state = AgentReachState.RATE_BLOCKED
        reason = "Agent Reach request rate limit is exhausted."
        heartbeat_current = False
    elif not budget_available:
        state = AgentReachState.BUDGET_BLOCKED
        reason = "Agent Reach request budget is exhausted."
        heartbeat_current = False
    elif heartbeat is None or heartbeat_age_seconds is None:
        state = AgentReachState.STALE
        reason = "No current Agent Reach heartbeat evidence is available."
        heartbeat_current = False
    else:
        if heartbeat.agent_id != identity.agent_id:
            raise AgentReachValidationError(
                "heartbeat agent does not match Agent Reach identity"
            )
        if heartbeat_age_seconds < 0:
            raise AgentReachValidationError(
                "Agent Reach heartbeat cannot originate in the future"
            )

        heartbeat_current = heartbeat_age_seconds <= stale_after_seconds

        if (
            heartbeat.worker_contract_schema
            != identity.worker_contract_schema
        ):
            state = AgentReachState.INCOMPATIBLE
            reason = "Heartbeat worker schema is incompatible."
            heartbeat_current = False
        elif not heartbeat_current:
            state = AgentReachState.STALE
            reason = "Agent Reach heartbeat exceeded the freshness window."
        elif not heartbeat.online:
            state = AgentReachState.OFFLINE
            reason = "Agent Reach heartbeat reports the agent offline."
        elif (
            heartbeat.active_requests
            >= request.limits.maximum_in_flight_requests
            or heartbeat.requests_last_hour
            >= request.limits.maximum_requests_per_hour
        ):
            state = AgentReachState.RATE_BLOCKED
            reason = "Agent heartbeat reports exhausted request capacity."
            rate_available = False
        else:
            state = AgentReachState.AVAILABLE
            reason = (
                "Agent identity, trust, capability, budget, and heartbeat "
                "evidence are compatible and current."
            )

    return AgentReachAssessment(
        agent_id=identity.agent_id,
        state=state,
        enabled=config.enabled,
        trust_sufficient=trust_sufficient,
        worker_contract_compatible=compatible,
        heartbeat_current=heartbeat_current,
        capability_allowed=capability_allowed,
        machine_supported=machine_supported,
        profile_supported=profile_supported,
        rate_available=rate_available,
        budget_available=budget_available,
        reason=reason,
        identity_digest=identity.identity_digest,
    )


def project_worker_job(
    config: AgentReachConfig,
    job: GovernedWorkerJob,
) -> AgentReachJobProjection:
    """Project one Stage 2 worker job into Agent Reach work state."""

    if job.integration_id != config.integration_id:
        raise AgentReachValidationError(
            "worker job integration does not match Agent Reach"
        )
    if job.schema_version != config.expected_worker_contract_schema:
        raise AgentReachValidationError(
            "worker job schema is incompatible with Agent Reach"
        )

    job.authority.validate()

    return AgentReachJobProjection(
        job_id=job.job_id,
        correlation_id=job.correlation_id,
        idempotency_key=job.idempotency_key,
        requested_capability=job.requested_capability,
        target_machine=job.target_machine,
        target_profile=job.target_profile,
        state=_JOB_STATE_PROJECTION[job.state],
        worker_contract_digest=job.contract_digest,
        worker_contract_schema=job.schema_version,
        created_at=job.created_at,
    )


def lifecycle_projection() -> Mapping[JobState, AgentReachWorkState]:
    """Expose a copy of the deterministic worker-to-reach state map."""

    return dict(_JOB_STATE_PROJECTION)
