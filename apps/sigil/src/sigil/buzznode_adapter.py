"""Disabled-by-default governed Buzznode worker-host adapter.

Stage 6 models persistent isolated worker hosts as immutable local projections
over the Stage 1 integration registry and Stage 2 worker/job contract.

This module performs no provisioning, network discovery, SSH, authentication,
credential resolution, browser launch, shell execution, filesystem access,
workspace creation, job dispatch, installation, activation, or financial action.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
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

BUZZNODE_ADAPTER_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_REPOSITORY = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
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


class BuzznodeValidationError(ValueError):
    """Buzznode adapter input failed closed."""


class BuzznodeRole(str, Enum):
    PERSISTENT_WORKER = "persistent_worker"
    BROWSER_WORKER = "browser_worker"
    BUILD_WORKER = "build_worker"
    RESEARCH_WORKER = "research_worker"


class BuzznodeLeaseState(str, Enum):
    UNASSIGNED = "unassigned"
    RESERVED = "reserved"
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    RELEASED = "released"
    INVALID = "invalid"


class BuzznodeHealth(str, Enum):
    DISABLED = "disabled"
    READY = "ready"
    BUSY = "busy"
    DEGRADED = "degraded"
    STALE = "stale"
    OFFLINE = "offline"
    INCOMPATIBLE = "incompatible"


class BuzznodeWorkState(str, Enum):
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


_JOB_STATE_PROJECTION: dict[JobState, BuzznodeWorkState] = {
    JobState.PROPOSED: BuzznodeWorkState.PROPOSED,
    JobState.ADMITTED: BuzznodeWorkState.ADMITTED,
    JobState.REJECTED: BuzznodeWorkState.REJECTED,
    JobState.QUEUED: BuzznodeWorkState.QUEUED,
    JobState.RUNNING: BuzznodeWorkState.RUNNING,
    JobState.CANCELLATION_REQUESTED: BuzznodeWorkState.CANCELLATION_REQUESTED,
    JobState.CANCELLED: BuzznodeWorkState.CANCELLED,
    JobState.SUCCEEDED: BuzznodeWorkState.SUCCEEDED,
    JobState.FAILED: BuzznodeWorkState.FAILED,
    JobState.COMPLETION_UNKNOWN: BuzznodeWorkState.COMPLETION_UNKNOWN,
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise BuzznodeValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise BuzznodeValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise BuzznodeValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise BuzznodeValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise BuzznodeValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise BuzznodeValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise BuzznodeValidationError(
            f"{label} must be a repository-relative reference"
        )


@dataclass(frozen=True, slots=True)
class BuzznodeAdapterConfig:
    integration_id: str = "buzznode"
    enabled: bool = False
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    schema_version: int = BUZZNODE_ADAPTER_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != BUZZNODE_ADAPTER_SCHEMA_VERSION:
            raise BuzznodeValidationError(
                "unsupported Buzznode adapter schema"
            )

        _require_identifier(self.integration_id, "Buzznode integration ID")

        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise BuzznodeValidationError(
                "incompatible worker contract schema"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Buzznode configuration")

    @property
    def can_provision(self) -> bool:
        return False

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_ssh(self) -> bool:
        return False

    @property
    def can_execute_shell(self) -> bool:
        return False

    @property
    def can_access_filesystem(self) -> bool:
        return False

    @property
    def can_launch_browser(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class BuzznodeIdentity:
    node_id: str
    machine_id: str
    display_name: str
    role: BuzznodeRole
    platform: str
    architecture: str
    worker_profile: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.node_id, "node ID"),
            (self.machine_id, "machine ID"),
            (self.platform, "platform"),
            (self.architecture, "architecture"),
            (self.worker_profile, "worker profile"),
        ):
            _require_identifier(value, label)

        if not self.display_name.strip():
            raise BuzznodeValidationError(
                "Buzznode display name is required"
            )
        if not isinstance(self.role, BuzznodeRole):
            raise BuzznodeValidationError("unknown Buzznode role")

        _validate_sanitized(asdict(self), "Buzznode identity")


@dataclass(frozen=True, slots=True)
class BuzznodeResourceLimits:
    cpu_cores: int
    memory_megabytes: int
    storage_megabytes: int
    maximum_concurrent_jobs: int
    maximum_runtime_seconds: int
    maximum_browser_sessions: int

    def __post_init__(self) -> None:
        if not 1 <= self.cpu_cores <= 256:
            raise BuzznodeValidationError(
                "CPU limit is outside bounds"
            )
        if not 256 <= self.memory_megabytes <= 2_097_152:
            raise BuzznodeValidationError(
                "memory limit is outside bounds"
            )
        if not 1024 <= self.storage_megabytes <= 100_000_000:
            raise BuzznodeValidationError(
                "storage limit is outside bounds"
            )
        if not 1 <= self.maximum_concurrent_jobs <= 100:
            raise BuzznodeValidationError(
                "concurrent job limit is outside bounds"
            )
        if not 1 <= self.maximum_runtime_seconds <= 604800:
            raise BuzznodeValidationError(
                "runtime limit is outside bounds"
            )
        if not 0 <= self.maximum_browser_sessions <= 100:
            raise BuzznodeValidationError(
                "browser-session limit is outside bounds"
            )


@dataclass(frozen=True, slots=True)
class BuzznodeCapabilitySet:
    capabilities: tuple[str, ...]
    browser_available: bool
    persistent_workspace_available: bool
    network_access_declared: bool
    credential_mount_available: bool = False
    shell_available: bool = False
    arbitrary_filesystem_available: bool = False

    def __post_init__(self) -> None:
        for capability in self.capabilities:
            _require_identifier(capability, "Buzznode capability")

        if len(set(self.capabilities)) != len(self.capabilities):
            raise BuzznodeValidationError(
                "duplicate Buzznode capability"
            )

        if self.credential_mount_available:
            raise BuzznodeValidationError(
                "credential mounts are prohibited in Stage 6"
            )
        if self.shell_available:
            raise BuzznodeValidationError(
                "shell authority is prohibited in Stage 6"
            )
        if self.arbitrary_filesystem_available:
            raise BuzznodeValidationError(
                "arbitrary filesystem authority is prohibited in Stage 6"
            )

        _validate_sanitized(asdict(self), "Buzznode capabilities")


@dataclass(frozen=True, slots=True)
class BuzznodeWorkspaceRef:
    workspace_id: str
    repository_identity: str
    revision: str
    workspace_reference: str
    persistent: bool
    isolated: bool
    read_only_reference: bool = True

    def __post_init__(self) -> None:
        _require_identifier(self.workspace_id, "workspace ID")

        if _REPOSITORY.fullmatch(self.repository_identity) is None:
            raise BuzznodeValidationError(
                "malformed workspace repository identity"
            )
        if _REVISION.fullmatch(self.revision) is None:
            raise BuzznodeValidationError(
                "workspace revision must be an immutable commit"
            )

        _validate_sanitized(asdict(self), "Buzznode workspace")
        _require_relative_reference(
            self.workspace_reference,
            "workspace reference",
        )

        if not self.isolated:
            raise BuzznodeValidationError(
                "Buzznode workspace must be isolated"
            )
        if not self.read_only_reference:
            raise BuzznodeValidationError(
                "Stage 6 workspace must remain a read-only reference"
            )


@dataclass(frozen=True, slots=True)
class BuzznodeBrowserSessionRef:
    session_id: str
    workspace_id: str
    browser_profile: str
    created_at: str
    expires_at: str
    state_reference: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.session_id, "browser session ID"),
            (self.workspace_id, "browser workspace ID"),
            (self.browser_profile, "browser profile"),
        ):
            _require_identifier(value, label)

        _require_timestamp(self.created_at, "browser session creation time")
        _require_timestamp(self.expires_at, "browser session expiry time")

        _validate_sanitized(asdict(self), "Buzznode browser session")
        _require_relative_reference(
            self.state_reference,
            "browser state reference",
        )


@dataclass(frozen=True, slots=True)
class BuzznodeLease:
    lease_id: str
    node_id: str
    job_id: str | None
    issued_at: str
    expires_at: str
    state: BuzznodeLeaseState
    generation: int

    def __post_init__(self) -> None:
        _require_identifier(self.lease_id, "lease ID")
        _require_identifier(self.node_id, "lease node ID")

        if self.job_id is not None:
            _require_identifier(self.job_id, "lease job ID")

        _require_timestamp(self.issued_at, "lease issue time")
        _require_timestamp(self.expires_at, "lease expiry time")

        if not isinstance(self.state, BuzznodeLeaseState):
            raise BuzznodeValidationError(
                "unknown Buzznode lease state"
            )
        if self.generation < 1:
            raise BuzznodeValidationError(
                "lease generation must be positive"
            )
        if self.state is BuzznodeLeaseState.UNASSIGNED and self.job_id is not None:
            raise BuzznodeValidationError(
                "unassigned lease cannot reference a job"
            )
        if self.state in {
            BuzznodeLeaseState.RESERVED,
            BuzznodeLeaseState.ACTIVE,
            BuzznodeLeaseState.EXPIRING,
        } and self.job_id is None:
            raise BuzznodeValidationError(
                "active lease state requires a job identity"
            )

        _validate_sanitized(asdict(self), "Buzznode lease")


@dataclass(frozen=True, slots=True)
class BuzznodeHeartbeat:
    node_id: str
    observed_at: str
    sequence: int
    online: bool
    running_jobs: int
    active_browser_sessions: int
    worker_contract_schema: int
    sanitized_summary: str

    def __post_init__(self) -> None:
        _require_identifier(self.node_id, "heartbeat node ID")
        _require_timestamp(self.observed_at, "heartbeat observation time")

        if self.sequence < 0:
            raise BuzznodeValidationError(
                "heartbeat sequence cannot be negative"
            )
        if self.running_jobs < 0:
            raise BuzznodeValidationError(
                "running job count cannot be negative"
            )
        if self.active_browser_sessions < 0:
            raise BuzznodeValidationError(
                "active browser session count cannot be negative"
            )
        if not self.sanitized_summary.strip():
            raise BuzznodeValidationError(
                "heartbeat summary is required"
            )

        _validate_sanitized(asdict(self), "Buzznode heartbeat")


@dataclass(frozen=True, slots=True)
class BuzznodeProjection:
    identity: BuzznodeIdentity
    resources: BuzznodeResourceLimits
    capabilities: BuzznodeCapabilitySet
    workspaces: tuple[BuzznodeWorkspaceRef, ...]
    browser_sessions: tuple[BuzznodeBrowserSessionRef, ...]
    lease: BuzznodeLease
    expected_worker_contract_schema: int
    schema_version: int = BUZZNODE_ADAPTER_SCHEMA_VERSION
    projection_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.projection_digest and self.projection_digest != expected:
            raise BuzznodeValidationError(
                "Buzznode projection digest mismatch"
            )
        if not self.projection_digest:
            object.__setattr__(self, "projection_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("projection_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != BUZZNODE_ADAPTER_SCHEMA_VERSION:
            raise BuzznodeValidationError(
                "unsupported Buzznode projection schema"
            )
        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise BuzznodeValidationError(
                "Buzznode projection worker schema is incompatible"
            )
        if self.lease.node_id != self.identity.node_id:
            raise BuzznodeValidationError(
                "Buzznode lease does not match node identity"
            )

        workspace_ids = {item.workspace_id for item in self.workspaces}

        if len(workspace_ids) != len(self.workspaces):
            raise BuzznodeValidationError(
                "duplicate Buzznode workspace identity"
            )

        if len({item.session_id for item in self.browser_sessions}) != len(
            self.browser_sessions
        ):
            raise BuzznodeValidationError(
                "duplicate browser session identity"
            )

        for session in self.browser_sessions:
            if session.workspace_id not in workspace_ids:
                raise BuzznodeValidationError(
                    "browser session references an unknown workspace"
                )

        if (
            len(self.browser_sessions)
            > self.resources.maximum_browser_sessions
        ):
            raise BuzznodeValidationError(
                "browser sessions exceed governed resource limits"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Buzznode projection",
        )

    @property
    def can_provision(self) -> bool:
        return False

    @property
    def can_execute(self) -> bool:
        return False

    @property
    def can_open_workspace(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class BuzznodeStatus:
    node_id: str
    state: BuzznodeHealth
    enabled: bool
    heartbeat_current: bool
    worker_contract_compatible: bool
    lease_valid: bool
    reason: str
    projection_digest: str
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.node_id, "status node ID")
        _require_digest(self.projection_digest, "projection digest")

        if not isinstance(self.state, BuzznodeHealth):
            raise BuzznodeValidationError(
                "unknown Buzznode health state"
            )
        if not self.reason.strip():
            raise BuzznodeValidationError(
                "Buzznode status reason is required"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Buzznode status")


@dataclass(frozen=True, slots=True)
class BuzznodeJobProjection:
    job_id: str
    correlation_id: str
    idempotency_key: str
    target_machine: str
    target_profile: str
    state: BuzznodeWorkState
    worker_contract_digest: str
    worker_contract_schema: int
    created_at: str
    schema_version: int = BUZZNODE_ADAPTER_SCHEMA_VERSION
    projection_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        for value, label in (
            (self.job_id, "job ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
            (self.target_machine, "target machine"),
            (self.target_profile, "target profile"),
        ):
            _require_identifier(value, label)

        if not isinstance(self.state, BuzznodeWorkState):
            raise BuzznodeValidationError(
                "unknown Buzznode work state"
            )

        _require_digest(
            self.worker_contract_digest,
            "worker contract digest",
        )
        _require_timestamp(self.created_at, "job creation time")

        if self.worker_contract_schema != WORKER_CONTRACT_SCHEMA_VERSION:
            raise BuzznodeValidationError(
                "Buzznode job projection worker schema is incompatible"
            )
        if self.schema_version != BUZZNODE_ADAPTER_SCHEMA_VERSION:
            raise BuzznodeValidationError(
                "unsupported Buzznode job projection schema"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Buzznode job projection",
        )

        expected = self.expected_digest()

        if self.projection_digest and self.projection_digest != expected:
            raise BuzznodeValidationError(
                "Buzznode job projection digest mismatch"
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


def validate_buzznode_registry_entry(
    config: BuzznodeAdapterConfig,
    entry: IntegrationRegistryEntry,
) -> None:
    """Validate that the Stage 1 registry describes Buzznode safely."""

    if entry.integration_id != config.integration_id:
        raise BuzznodeValidationError(
            "Buzznode registry identity mismatch"
        )
    if entry.category is not IntegrationCategory.WORKER:
        raise BuzznodeValidationError(
            "Buzznode must be registered as a worker integration"
        )
    if entry.lifecycle_state in {
        LifecycleState.REJECTED,
        LifecycleState.DEPRECATED,
        LifecycleState.QUARANTINED,
    }:
        raise BuzznodeValidationError(
            "Buzznode registry lifecycle is not eligible"
        )

    entry.authority.validate()

    if entry.can_activate:
        raise BuzznodeValidationError(
            "Buzznode registry entry unexpectedly permits activation"
        )


def evaluate_buzznode_status(
    config: BuzznodeAdapterConfig,
    projection: BuzznodeProjection,
    *,
    heartbeat: BuzznodeHeartbeat | None,
    heartbeat_age_seconds: int | None,
    lease_age_seconds: int | None,
    stale_after_seconds: int = 120,
) -> BuzznodeStatus:
    """Evaluate injected Buzznode evidence without contacting or provisioning it."""

    if not 1 <= stale_after_seconds <= 86400:
        raise BuzznodeValidationError(
            "heartbeat staleness threshold is outside bounds"
        )

    if not config.enabled:
        return BuzznodeStatus(
            node_id=projection.identity.node_id,
            state=BuzznodeHealth.DISABLED,
            enabled=False,
            heartbeat_current=False,
            worker_contract_compatible=True,
            lease_valid=False,
            reason="Buzznode adapter is disabled by policy.",
            projection_digest=projection.projection_digest,
        )

    if heartbeat is None or heartbeat_age_seconds is None:
        return BuzznodeStatus(
            node_id=projection.identity.node_id,
            state=BuzznodeHealth.STALE,
            enabled=True,
            heartbeat_current=False,
            worker_contract_compatible=False,
            lease_valid=False,
            reason="No current Buzznode heartbeat evidence is available.",
            projection_digest=projection.projection_digest,
        )

    if heartbeat.node_id != projection.identity.node_id:
        raise BuzznodeValidationError(
            "heartbeat node does not match Buzznode projection"
        )
    if heartbeat_age_seconds < 0:
        raise BuzznodeValidationError(
            "Buzznode heartbeat cannot originate in the future"
        )
    if lease_age_seconds is not None and lease_age_seconds < 0:
        raise BuzznodeValidationError(
            "Buzznode lease evidence cannot originate in the future"
        )

    compatible = (
        heartbeat.worker_contract_schema
        == projection.expected_worker_contract_schema
    )

    if not compatible:
        state = BuzznodeHealth.INCOMPATIBLE
        reason = "Buzznode worker contract schema is incompatible."
        current = heartbeat_age_seconds <= stale_after_seconds
        lease_valid = False
    elif heartbeat_age_seconds > stale_after_seconds:
        state = BuzznodeHealth.STALE
        reason = "Buzznode heartbeat evidence exceeded the freshness window."
        current = False
        lease_valid = False
    elif not heartbeat.online:
        state = BuzznodeHealth.OFFLINE
        reason = "Buzznode heartbeat reports the node offline."
        current = True
        lease_valid = False
    else:
        current = True
        lease_valid = projection.lease.state in {
            BuzznodeLeaseState.UNASSIGNED,
            BuzznodeLeaseState.RESERVED,
            BuzznodeLeaseState.ACTIVE,
            BuzznodeLeaseState.EXPIRING,
        }

        if projection.lease.state in {
            BuzznodeLeaseState.EXPIRED,
            BuzznodeLeaseState.RELEASED,
            BuzznodeLeaseState.INVALID,
        }:
            state = BuzznodeHealth.DEGRADED
            reason = "Buzznode lease is not currently valid."
            lease_valid = False
        elif (
            heartbeat.running_jobs
            > projection.resources.maximum_concurrent_jobs
            or heartbeat.active_browser_sessions
            > projection.resources.maximum_browser_sessions
        ):
            state = BuzznodeHealth.DEGRADED
            reason = "Buzznode heartbeat exceeds governed resource limits."
            lease_valid = False
        elif heartbeat.running_jobs > 0:
            state = BuzznodeHealth.BUSY
            reason = "Buzznode is online with governed work in progress."
        else:
            state = BuzznodeHealth.READY
            reason = "Buzznode is online, compatible, and within limits."

    return BuzznodeStatus(
        node_id=projection.identity.node_id,
        state=state,
        enabled=True,
        heartbeat_current=current,
        worker_contract_compatible=compatible,
        lease_valid=lease_valid,
        reason=reason,
        projection_digest=projection.projection_digest,
    )


def project_worker_job(
    config: BuzznodeAdapterConfig,
    job: GovernedWorkerJob,
) -> BuzznodeJobProjection:
    """Project one Stage 2 worker job into a descriptive Buzznode state."""

    if job.integration_id != config.integration_id:
        raise BuzznodeValidationError(
            "worker job integration does not match Buzznode"
        )
    if job.schema_version != config.expected_worker_contract_schema:
        raise BuzznodeValidationError(
            "worker job schema is incompatible with Buzznode"
        )

    job.authority.validate()

    return BuzznodeJobProjection(
        job_id=job.job_id,
        correlation_id=job.correlation_id,
        idempotency_key=job.idempotency_key,
        target_machine=job.target_machine,
        target_profile=job.target_profile,
        state=_JOB_STATE_PROJECTION[job.state],
        worker_contract_digest=job.contract_digest,
        worker_contract_schema=job.schema_version,
        created_at=job.created_at,
    )


def lifecycle_projection() -> Mapping[JobState, BuzznodeWorkState]:
    """Expose a copy of the deterministic worker-to-Buzznode state map."""

    return dict(_JOB_STATE_PROJECTION)
