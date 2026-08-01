"""Governed, advisory-only Titan/Mac/Prime fleet placement and evidence."""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import ClassVar, Protocol

from .models import (
    PROHIBITED_RESPONSIBILITIES,
    Capability,
    CostClass,
    ExecutionLocation,
    PrivacyTier,
    ProviderHealth,
    Responsibility,
    TrustTier,
    validate_identifier,
)
from .orchestration import (
    GovernedOrchestrationRequest,
    GovernedOrchestrationStep,
    GovernedStepResult,
    OrchestrationStepStatus,
    WorkerTaskType,
)
from .registry import canonical_digest

FLEET_SCHEMA_VERSION = 1
MAX_CLOCK_SKEW_SECONDS = 120
MAX_HEARTBEAT_AGE_SECONDS = 180
MAX_REMOTE_PAYLOAD_CHARS = 16_384
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SENSITIVE = ("api_key", "authorization", "bearer ", "password", "secret", "token=")
_ZERO_HASH = "0" * 64


class FleetValidationError(ValueError):
    """Fleet input failed closed."""


class FleetStoreError(RuntimeError):
    """Fleet evidence could not be safely read or written."""


class FleetConflictError(FleetStoreError):
    """An immutable fleet identity was reused."""


class NoEligibleFleetNodeError(FleetValidationError):
    """No authenticated node satisfies the routing policy."""


class FleetNodeRole(str, Enum):
    LOCAL_BACKEND = "local_backend"
    TITAN = "titan"
    MAC = "mac"
    PRIME = "prime"
    EXTERNAL_FALLBACK = "external_fallback"


class DeviceClass(str, Enum):
    COORDINATOR = "coordinator"
    WORKSTATION = "workstation"
    SERVER = "server"
    ISOLATED_WORKER = "isolated_worker"


class MemoryClass(IntEnum):
    SMALL = 1
    MEDIUM = 2
    LARGE = 3
    XLARGE = 4


class CPUClass(IntEnum):
    LIGHT = 1
    STANDARD = 2
    HIGH = 3
    ACCELERATED = 4


class FleetNodeState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class RemoteTaskState(str, Enum):
    NOT_STARTED = "not_started"
    ACKNOWLEDGED = "acknowledged"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLATION_REQUESTED = "cancellation_requested"
    COMPLETION_UNKNOWN = "completion_unknown"


TERMINAL_REMOTE_STATES = frozenset(
    {RemoteTaskState.SUCCEEDED, RemoteTaskState.FAILED, RemoteTaskState.CANCELLED}
)


def _bounded(value: str, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FleetValidationError(f"{field} is invalid")
    lowered = value.lower()
    if any(marker in lowered for marker in _SENSITIVE):
        raise FleetValidationError(f"{field} contains prohibited material")
    return value.strip()


def _digests(values: Sequence[str], field: str) -> tuple[str, ...]:
    result = tuple(values)
    if not result or len(result) > 64 or any(_SHA256.fullmatch(item) is None for item in result):
        raise FleetValidationError(f"{field} requires bounded digest references")
    return result


def _no_authority(value: object, field: str) -> None:
    if getattr(value, "paper_only", None) is not True or any(
        getattr(value, name, False)
        for name in (
            "broker_submission",
            "execution_authorized",
            "portfolio_mutation",
            "approval_authority",
            "shell_allowed",
            "arbitrary_code_allowed",
            "credentials_available",
            "broker_access",
            "portfolio_access",
            "recursive_workers_allowed",
        )
    ):
        raise FleetValidationError(f"{field} cannot carry authority")


@dataclass(frozen=True, slots=True)
class FleetNodeIdentity:
    node_id: str
    node_name: str
    node_role: FleetNodeRole
    device_class: DeviceClass
    platform: str
    architecture: str
    operating_system: str
    trust_tier: TrustTier
    privacy_tier: PrivacyTier
    execution_location: ExecutionLocation
    transport_identity: str
    authenticated_identity_ref: str
    registered_at: str
    last_seen_at: str
    enabled: bool = False
    authenticated: bool = False
    schema_version: int = FLEET_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        for value, field in ((self.node_id, "node_id"), (self.node_name, "node_name")):
            validate_identifier(value, field)
        for value, field in (
            (self.platform, "platform"),
            (self.architecture, "architecture"),
            (self.operating_system, "operating_system"),
            (self.transport_identity, "transport_identity"),
            (self.authenticated_identity_ref, "authenticated_identity_ref"),
        ):
            _bounded(value, field, 256)
        if self.execution_location not in {ExecutionLocation.LOCAL, ExecutionLocation.FLEET}:
            raise FleetValidationError("fleet node execution location is invalid")
        if not self.transport_identity.startswith(("tailnet:", "local-tls:")):
            raise FleetValidationError(
                "fleet transport identity must be authenticated and encrypted"
            )
        if self.enabled and not self.authenticated:
            raise FleetValidationError("enabled fleet nodes require authenticated identity")
        _no_authority(self, "fleet node identity")


@dataclass(frozen=True, slots=True)
class FleetModelInventory:
    provider_id: str
    model_id: str
    tokenizer_id: str | None
    capabilities: frozenset[Capability]
    vector_dimension: int | None = None
    corpus_revision: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.provider_id, "provider_id")
        validate_identifier(self.model_id, "model_id")
        if self.tokenizer_id is not None:
            validate_identifier(self.tokenizer_id, "tokenizer_id")
        if not self.capabilities or (
            self.vector_dimension is not None and self.vector_dimension < 1
        ):
            raise FleetValidationError("fleet model inventory is invalid")
        if self.corpus_revision is not None and _SHA256.fullmatch(self.corpus_revision) is None:
            raise FleetValidationError("corpus revision must be a digest")


@dataclass(frozen=True, slots=True)
class FleetNodeRegistration:
    identity: FleetNodeIdentity
    models: tuple[FleetModelInventory, ...]
    supported_task_types: frozenset[WorkerTaskType]
    memory_class: MemoryClass
    cpu_class: CPUClass
    accelerator_class: str | None
    maximum_concurrency: int
    maximum_task_duration_ms: int
    maximum_input_chars: int
    maximum_output_chars: int
    network_scope: str = "authenticated_fleet_only"
    filesystem_scope: str = "digest_cache_only"
    shell_allowed: bool = False
    arbitrary_code_allowed: bool = False
    credentials_available: bool = False
    broker_access: bool = False
    portfolio_access: bool = False
    recursive_workers_allowed: bool = False
    resource_enforcement_verified: bool = False
    enabled: bool = False
    health: ProviderHealth = ProviderHealth.UNAVAILABLE
    current_load: int = 0
    draining: bool = False
    maintenance: bool = False
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            not self.models
            or not 1 <= self.maximum_concurrency <= 16
            or not 100 <= self.maximum_task_duration_ms <= 300_000
            or not 1 <= self.maximum_input_chars <= MAX_REMOTE_PAYLOAD_CHARS
            or not 1 <= self.maximum_output_chars <= MAX_REMOTE_PAYLOAD_CHARS
            or not 0 <= self.current_load <= 100
            or self.network_scope != "authenticated_fleet_only"
            or self.filesystem_scope != "digest_cache_only"
        ):
            raise FleetValidationError("fleet node registration is unsafe or unbounded")
        if (
            self.enabled != self.identity.enabled
            or self.enabled
            and not self.identity.authenticated
        ):
            raise FleetValidationError("fleet registration identity state is inconsistent")
        _no_authority(self, "fleet node registration")

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(capability for model in self.models for capability in model.capabilities)


@dataclass(frozen=True, slots=True)
class FleetNodeHealth:
    node_id: str
    authenticated_identity_ref: str
    observed_at: str
    node_timestamp: str
    state: FleetNodeState
    available_capabilities: frozenset[Capability]
    available_model_ids: tuple[str, ...]
    current_load: int
    active_tasks: int
    queue_depth: int
    memory_pressure: str
    disk_pressure: str
    thermal_state: str
    transport_health: ProviderHealth
    maintenance: bool
    draining: bool
    last_failure: str | None = None
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.node_id, "node_id")
        _bounded(self.authenticated_identity_ref, "authenticated identity", 256)
        for value in (self.memory_pressure, self.disk_pressure, self.thermal_state):
            if value not in {"normal", "elevated", "critical", "unknown"}:
                raise FleetValidationError("fleet health pressure is invalid")
        if (
            not 0 <= self.current_load <= 100
            or not 0 <= self.active_tasks <= 16
            or not 0 <= self.queue_depth <= 100
        ):
            raise FleetValidationError("fleet health load is unbounded")
        if self.last_failure is not None:
            _bounded(self.last_failure, "last_failure")
        _no_authority(self, "fleet health")

    def freshness(self, *, coordinator_time: str) -> str:
        coordinator = datetime.fromisoformat(coordinator_time)
        observed = datetime.fromisoformat(self.observed_at)
        node = datetime.fromisoformat(self.node_timestamp)
        if abs((node - observed).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
            return "clock_skew"
        if (coordinator - observed).total_seconds() > MAX_HEARTBEAT_AGE_SECONDS:
            return "stale"
        if observed > coordinator:
            return "future"
        return "current"


@dataclass(frozen=True, slots=True)
class FleetRoutingRequest:
    fleet_request_id: str
    orchestration_id: str
    step_id: str
    task_correlation_id: str
    required_capability: Capability
    responsibility: Responsibility
    required_provider_id: str | None
    required_model_id: str | None
    required_tokenizer_id: str | None
    required_vector_dimension: int | None
    required_corpus_revision: str | None
    privacy_requirement: PrivacyTier
    minimum_trust_tier: TrustTier
    preferred_node_roles: tuple[FleetNodeRole, ...]
    excluded_node_ids: tuple[str, ...]
    maximum_latency_ms: int
    maximum_duration_ms: int
    maximum_memory_class: MemoryClass
    minimum_cpu_class: CPUClass
    maximum_cost_class: CostClass
    fallback_permission: bool
    escalation_permission: bool
    cancellation_policy: str
    maximum_retries: int
    maximum_remote_steps: int
    input_digests: tuple[str, ...]
    evidence_context_digests: tuple[str, ...]
    requested_at: str
    schema_version: int = FLEET_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.fleet_request_id, "fleet_request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        if self.responsibility in PROHIBITED_RESPONSIBILITIES:
            raise FleetValidationError("fleet request responsibility is prohibited")
        for value, field in (
            (self.required_provider_id, "provider_id"),
            (self.required_model_id, "model_id"),
            (self.required_tokenizer_id, "tokenizer_id"),
        ):
            if value is not None:
                validate_identifier(value, field)
        if (
            self.required_corpus_revision is not None
            and _SHA256.fullmatch(self.required_corpus_revision) is None
        ):
            raise FleetValidationError("fleet corpus revision is invalid")
        if (
            not self.preferred_node_roles
            or len(set(self.preferred_node_roles)) != len(self.preferred_node_roles)
            or not 10 <= self.maximum_latency_ms <= 120_000
            or not 100 <= self.maximum_duration_ms <= 300_000
            or not 0 <= self.maximum_retries <= 1
            or not 1 <= self.maximum_remote_steps <= 4
            or self.cancellation_policy not in {"query_before_retry", "cancel_on_timeout"}
        ):
            raise FleetValidationError("fleet request is unsafe or unbounded")
        _digests(self.input_digests, "fleet input")
        _digests(self.evidence_context_digests, "fleet evidence")
        _no_authority(self, "fleet routing request")


@dataclass(frozen=True, slots=True)
class FleetNodeConsideration:
    node_id: str
    eligible: bool
    reasons: tuple[str, ...]
    provider_id: str | None
    model_id: str | None
    estimated_latency_ms: int
    load: int


@dataclass(frozen=True, slots=True)
class FleetRoutingDecision:
    fleet_decision_id: str
    fleet_request_id: str
    selected_node_id: str | None
    selected_provider_id: str | None
    selected_model_id: str | None
    considered_nodes: tuple[FleetNodeConsideration, ...]
    registry_revision: str
    fallback_used: bool
    escalation_used: bool
    locality_constraints: tuple[str, ...]
    created_at: str
    evidence_identity: str
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.fleet_decision_id, "fleet_decision_id")
        _digests((self.registry_revision, self.evidence_identity), "fleet decision evidence")
        _no_authority(self, "fleet routing decision")


@dataclass(frozen=True, slots=True)
class GovernedRemoteTask:
    remote_task_id: str
    fleet_request_id: str
    orchestration_id: str
    step_id: str
    node_id: str
    task_type: WorkerTaskType
    capability: Capability
    input_digests: tuple[str, ...]
    expected_output_schema: str
    timeout_ms: int
    memory_class: MemoryClass
    cpu_class: CPUClass
    maximum_input_chars: int
    maximum_output_chars: int
    privacy_requirement: PrivacyTier
    trust_requirement: TrustTier
    cancellation_token_id: str
    requested_at: str
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        for value, field in (
            (self.remote_task_id, "remote_task_id"),
            (self.cancellation_token_id, "cancellation_token_id"),
        ):
            validate_identifier(value, field)
        _digests(self.input_digests, "remote task input")
        if (
            not self.expected_output_schema.startswith("sigil.ai.output.")
            or not 100 <= self.timeout_ms <= 300_000
        ):
            raise FleetValidationError("remote task is unsafe or unbounded")
        _no_authority(self, "remote task")


@dataclass(frozen=True, slots=True)
class GovernedRemoteResult:
    remote_result_id: str
    remote_task_id: str
    node_id: str
    provider_id: str
    model_id: str
    started_at: str
    ended_at: str
    state: RemoteTaskState
    structured_payload: tuple[tuple[str, str], ...]
    input_digest: str
    output_digest: str | None
    resource_usage: tuple[tuple[str, str], ...]
    cancellation_state: str
    limitations: tuple[str, ...]
    evidence_identity: str
    failure_classification: str | None = None
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False

    def __post_init__(self) -> None:
        for value, field in (
            (self.remote_result_id, "remote_result_id"),
            (self.remote_task_id, "remote_task_id"),
        ):
            validate_identifier(value, field)
        _digests((self.input_digest, self.evidence_identity), "remote result evidence")
        if self.output_digest is not None and _SHA256.fullmatch(self.output_digest) is None:
            raise FleetValidationError("remote output digest is invalid")
        if (
            tuple(sorted(self.structured_payload)) != self.structured_payload
            or len(str(self.structured_payload)) > MAX_REMOTE_PAYLOAD_CHARS
        ):
            raise FleetValidationError("remote result payload is unsafe")
        if any(marker in str(self.structured_payload).lower() for marker in _SENSITIVE):
            raise FleetValidationError("remote result contains prohibited material")
        if self.state == RemoteTaskState.SUCCEEDED and (
            self.output_digest is None or self.failure_classification is not None
        ):
            raise FleetValidationError("successful remote result is contradictory")
        _no_authority(self, "remote result")


@dataclass(frozen=True, slots=True)
class FleetEvidence:
    evidence_id: str
    event_type: str
    subject_id: str
    node_id: str | None
    input_digest: str
    output_digest: str | None
    state: str
    failure_classification: str | None
    created_at: str
    details: tuple[tuple[str, str], ...] = ()
    schema_version: int = FLEET_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        _digests((self.evidence_id, self.input_digest), "fleet evidence")
        if self.output_digest is not None and _SHA256.fullmatch(self.output_digest) is None:
            raise FleetValidationError("fleet evidence output digest is invalid")
        _bounded(self.event_type, "fleet event type", 80)
        _bounded(self.subject_id, "fleet subject", 128)
        if tuple(sorted(self.details)) != self.details or len(self.details) > 20:
            raise FleetValidationError("fleet evidence details are invalid")
        if len(json.dumps(self.details, separators=(",", ":"))) > 12_000:
            raise FleetValidationError("fleet evidence details exceed their bound")
        if any(marker in str(self.details).lower() for marker in _SENSITIVE):
            raise FleetValidationError("fleet evidence contains prohibited material")
        _no_authority(self, "fleet evidence")


class DurableFleetStore:
    """Separate hash-chained evidence journal for fleet state and recovery."""

    def __init__(self, state_root: Path) -> None:
        if not state_root.is_absolute() or not state_root.is_dir() or state_root.is_symlink():
            raise FleetStoreError("fleet state root is unsafe")
        self.directory = state_root / "governed-ai-fleet-v1"
        self.path = self.directory / "fleet-evidence.jsonl"
        self.lock_path = self.directory / "fleet-evidence.lock"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise FleetStoreError("fleet store paths are unsafe")
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def append(self, evidence: FleetEvidence) -> FleetEvidence:
        with self._locked():
            records = self._read_unlocked(True)
            if any(item.evidence_id == evidence.evidence_id for item in records):
                raise FleetConflictError("duplicate fleet evidence identity")
            terminal_subjects = {
                item.subject_id
                for item in records
                if item.event_type in {"task_result", "cancellation_reconciled"}
                and item.state in {state.value for state in TERMINAL_REMOTE_STATES}
            }
            if evidence.subject_id in terminal_subjects and evidence.event_type in {
                "task_dispatch",
                "task_acknowledged",
                "task_started",
                "task_result",
                "cancellation_requested",
                "cancellation_reconciled",
            }:
                raise FleetConflictError("terminal remote task evidence is immutable")
            previous = _ZERO_HASH if not self.path.exists() else self._last_hash()
            record = asdict(evidence)
            envelope = {
                "store_version": FLEET_SCHEMA_VERSION,
                "sequence": len(records) + 1,
                "previous_entry_hash": previous,
                "record": record,
                "entry_hash": "",
            }
            envelope["entry_hash"] = canonical_digest(
                {key: value for key, value in envelope.items() if key != "entry_hash"}
            )
            encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
            try:
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise FleetStoreError("fleet evidence write made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return evidence

    def read(self, *, recover_truncated_tail: bool = True) -> tuple[FleetEvidence, ...]:
        with self._locked():
            return self._read_unlocked(recover_truncated_tail)

    def _read_unlocked(self, recover: bool) -> tuple[FleetEvidence, ...]:
        if not self.path.exists():
            return ()
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if not recover:
                raise FleetStoreError("fleet store has truncated tail")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.ftruncate(descriptor, boundary)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            raw = raw[:boundary]
        records: list[FleetEvidence] = []
        previous = _ZERO_HASH
        for index, line in enumerate(raw.splitlines(), 1):
            try:
                envelope = json.loads(line)
                actual = canonical_digest(
                    {key: value for key, value in envelope.items() if key != "entry_hash"}
                )
                if (
                    envelope["store_version"] != FLEET_SCHEMA_VERSION
                    or envelope["sequence"] != index
                    or envelope["previous_entry_hash"] != previous
                    or envelope["entry_hash"] != actual
                ):
                    raise FleetStoreError("fleet evidence chain is corrupt")
                payload = envelope["record"]
                payload["details"] = tuple(tuple(item) for item in payload["details"])
                records.append(FleetEvidence(**payload))
                previous = envelope["entry_hash"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise FleetStoreError("fleet evidence is corrupt") from exc
        return tuple(records)

    def _last_hash(self) -> str:
        lines = self.path.read_bytes().splitlines()
        return json.loads(lines[-1])["entry_hash"] if lines else _ZERO_HASH


def _registration_payload(registration: FleetNodeRegistration) -> dict[str, object]:
    identity = asdict(registration.identity)
    for field in (
        "node_role",
        "device_class",
        "trust_tier",
        "privacy_tier",
        "execution_location",
    ):
        identity[field] = getattr(registration.identity, field).value
    models = []
    for item in registration.models:
        values = asdict(item)
        values["capabilities"] = sorted(capability.value for capability in item.capabilities)
        models.append(values)
    payload = asdict(registration)
    payload.update(
        {
            "identity": identity,
            "models": models,
            "supported_task_types": sorted(
                item.value for item in registration.supported_task_types
            ),
            "memory_class": registration.memory_class.value,
            "cpu_class": registration.cpu_class.value,
            "health": registration.health.value,
        }
    )
    return payload


def _decode_registration(payload: Mapping[str, object]) -> FleetNodeRegistration:
    identity_payload = dict(payload["identity"])
    identity = FleetNodeIdentity(
        **{
            **identity_payload,
            "node_role": FleetNodeRole(identity_payload["node_role"]),
            "device_class": DeviceClass(identity_payload["device_class"]),
            "trust_tier": TrustTier(identity_payload["trust_tier"]),
            "privacy_tier": PrivacyTier(identity_payload["privacy_tier"]),
            "execution_location": ExecutionLocation(identity_payload["execution_location"]),
        }
    )
    return FleetNodeRegistration(
        **{
            **payload,
            "identity": identity,
            "models": tuple(
                FleetModelInventory(
                    **{
                        **item,
                        "capabilities": frozenset(
                            Capability(value) for value in item["capabilities"]
                        ),
                    }
                )
                for item in payload["models"]
            ),
            "supported_task_types": frozenset(
                WorkerTaskType(value) for value in payload["supported_task_types"]
            ),
            "memory_class": MemoryClass(payload["memory_class"]),
            "cpu_class": CPUClass(payload["cpu_class"]),
            "health": ProviderHealth(payload["health"]),
        }
    )


class FleetRegistry:
    def __init__(self, registrations: Sequence[FleetNodeRegistration]) -> None:
        self.registrations = tuple(registrations)
        identities = [item.identity.node_id for item in self.registrations]
        auth_refs = [item.identity.authenticated_identity_ref for item in self.registrations]
        if len(identities) != len(set(identities)) or len(auth_refs) != len(set(auth_refs)):
            raise FleetConflictError("duplicate fleet node or authenticated identity")
        self.revision = (
            f"sha256:{canonical_digest(tuple(asdict(item) for item in self.registrations))}"
        )

    def authenticate(self, node_id: str, authenticated_identity_ref: str) -> FleetNodeRegistration:
        matches = [item for item in self.registrations if item.identity.node_id == node_id]
        if (
            len(matches) != 1
            or matches[0].identity.authenticated_identity_ref != authenticated_identity_ref
            or not matches[0].identity.authenticated
        ):
            raise FleetValidationError("fleet node authentication failed")
        return matches[0]

    def persist(self, store: DurableFleetStore, *, registered_at: str) -> None:
        existing = {
            item.node_id
            for item in store.read()
            if item.event_type == "node_registration" and item.node_id is not None
        }
        for registration in self.registrations:
            if registration.identity.node_id in existing:
                raise FleetConflictError("duplicate durable fleet node registration")
            payload = _registration_payload(registration)
            store.append(
                fleet_evidence(
                    "node_registration",
                    registration.identity.node_id,
                    node_id=registration.identity.node_id,
                    input_value=registration.identity.authenticated_identity_ref,
                    output_value=payload,
                    state="registered",
                    created_at=registered_at,
                    details={
                        "registration": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                        "role": registration.identity.node_role.value,
                    },
                )
            )

    @classmethod
    def recover(cls, store: DurableFleetStore) -> FleetRegistry:
        registrations = []
        for item in store.read():
            if item.event_type != "node_registration":
                continue
            payload = dict(item.details).get("registration")
            if payload is None:
                raise FleetStoreError("fleet registration evidence cannot be recovered")
            try:
                registrations.append(_decode_registration(json.loads(payload)))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise FleetStoreError("fleet registration evidence is corrupt") from error
        return cls(registrations)


class GovernedFleetRouter:
    _ROLE_ORDER: ClassVar[dict[FleetNodeRole, int]] = {
        FleetNodeRole.LOCAL_BACKEND: 0,
        FleetNodeRole.TITAN: 1,
        FleetNodeRole.MAC: 2,
        FleetNodeRole.PRIME: 3,
        FleetNodeRole.EXTERNAL_FALLBACK: 4,
    }

    def __init__(self, registry: FleetRegistry) -> None:
        self.registry = registry

    def route(
        self,
        request: FleetRoutingRequest,
        health: Mapping[str, FleetNodeHealth],
        *,
        decided_at: str,
    ) -> FleetRoutingDecision:
        considerations: list[FleetNodeConsideration] = []
        eligible: list[tuple[tuple[int, int, str], FleetNodeRegistration, FleetModelInventory]] = []
        for node in sorted(self.registry.registrations, key=lambda item: item.identity.node_id):
            reasons: list[str] = []
            heartbeat = health.get(node.identity.node_id)
            model = next((item for item in node.models if self._model_matches(item, request)), None)
            if node.identity.node_id in request.excluded_node_ids:
                reasons.append("operator_excluded")
            if not node.enabled or not node.identity.authenticated:
                reasons.append("disabled_or_unauthenticated")
            if node.maintenance or node.draining:
                reasons.append("maintenance_or_draining")
            if (
                heartbeat is None
                or heartbeat.authenticated_identity_ref != node.identity.authenticated_identity_ref
            ):
                reasons.append("missing_or_spoofed_heartbeat")
            elif heartbeat.freshness(coordinator_time=decided_at) != "current":
                reasons.append(heartbeat.freshness(coordinator_time=decided_at))
            elif (
                heartbeat.state == FleetNodeState.UNAVAILABLE
                or heartbeat.transport_health == ProviderHealth.UNAVAILABLE
            ):
                reasons.append("unavailable")
            if request.privacy_requirement > node.identity.privacy_tier:
                reasons.append("privacy_mismatch")
            if request.minimum_trust_tier > node.identity.trust_tier:
                reasons.append("trust_mismatch")
            if (
                node.memory_class < request.maximum_memory_class
                or node.cpu_class < request.minimum_cpu_class
                or node.maximum_task_duration_ms < request.maximum_duration_ms
            ):
                reasons.append("resource_mismatch")
            if (
                request.maximum_memory_class >= MemoryClass.LARGE
                and not node.resource_enforcement_verified
            ):
                reasons.append("resource_enforcement_unverified")
            if model is None:
                reasons.append("capability_or_model_mismatch")
            load = node.current_load if heartbeat is None else heartbeat.current_load
            considerations.append(
                FleetNodeConsideration(
                    node.identity.node_id,
                    not reasons,
                    tuple(reasons),
                    None if model is None else model.provider_id,
                    None if model is None else model.model_id,
                    10 + load * 2,
                    load,
                )
            )
            if not reasons and model is not None:
                role_rank = self._ROLE_ORDER[node.identity.node_role]
                if (
                    request.preferred_node_roles
                    and node.identity.node_role in request.preferred_node_roles
                ):
                    role_rank = request.preferred_node_roles.index(node.identity.node_role)
                if (
                    request.minimum_cpu_class >= CPUClass.HIGH
                    and node.identity.node_role == FleetNodeRole.MAC
                ):
                    role_rank = -1
                eligible.append(((role_rank, load, node.identity.node_id), node, model))
        selected = min(eligible, default=None, key=lambda item: item[0])
        if selected is None and not request.fallback_permission:
            raise NoEligibleFleetNodeError("no governed fleet node is eligible")
        node = None if selected is None else selected[1]
        model = None if selected is None else selected[2]
        values = {
            "request": request.fleet_request_id,
            "node": None if node is None else node.identity.node_id,
            "model": None if model is None else model.model_id,
            "considered": tuple(asdict(item) for item in considerations),
            "registry": self.registry.revision,
        }
        digest = canonical_digest(values)
        selected_role = None if node is None else node.identity.node_role
        return FleetRoutingDecision(
            f"fleet-decision-{digest[:64]}",
            request.fleet_request_id,
            None if node is None else node.identity.node_id,
            None if model is None else model.provider_id,
            None if model is None else model.model_id,
            tuple(considerations),
            self.registry.revision,
            selected is None,
            selected_role not in {None, FleetNodeRole.LOCAL_BACKEND, FleetNodeRole.TITAN},
            tuple(
                item
                for item in (
                    "digest_only",
                    "private_evidence_stays_eligible",
                    "exact_model_compatibility",
                )
                if item
            ),
            decided_at,
            f"sha256:{canonical_digest({'decision': digest})}",
        )

    @staticmethod
    def _model_matches(model: FleetModelInventory, request: FleetRoutingRequest) -> bool:
        return (
            request.required_capability in model.capabilities
            and (
                request.required_provider_id is None
                or request.required_provider_id == model.provider_id
            )
            and (request.required_model_id is None or request.required_model_id == model.model_id)
            and (
                request.required_tokenizer_id is None
                or request.required_tokenizer_id == model.tokenizer_id
            )
            and (
                request.required_vector_dimension is None
                or request.required_vector_dimension == model.vector_dimension
            )
            and (
                request.required_corpus_revision is None
                or request.required_corpus_revision == model.corpus_revision
            )
        )


class FleetTransportAdapter(Protocol):
    def dispatch(self, task: GovernedRemoteTask) -> GovernedRemoteResult: ...
    def cancel(self, task_id: str, cancellation_token_id: str) -> RemoteTaskState: ...
    def query(self, task_id: str) -> GovernedRemoteResult | None: ...


class GovernedFleetTransport:
    """Authenticated adapter boundary; no endpoint, credential, command, or code input."""

    def __init__(
        self, registry: FleetRegistry, adapters: Mapping[str, FleetTransportAdapter]
    ) -> None:
        self.registry = registry
        self.adapters = dict(adapters)
        if set(self.adapters) - {item.identity.node_id for item in registry.registrations}:
            raise FleetValidationError("transport contains unknown nodes")
        self._requests: set[str] = set()

    def dispatch(
        self, task: GovernedRemoteTask, *, authenticated_identity_ref: str
    ) -> GovernedRemoteResult:
        registration = self.registry.authenticate(task.node_id, authenticated_identity_ref)
        if task.remote_task_id in self._requests:
            raise FleetConflictError("duplicate remote task or replay attempt")
        if (
            task.node_id not in self.adapters
            or task.timeout_ms > registration.maximum_task_duration_ms
        ):
            raise FleetValidationError("remote transport unavailable or task out of bounds")
        self._requests.add(task.remote_task_id)
        result = self.adapters[task.node_id].dispatch(task)
        if result.node_id != task.node_id or result.remote_task_id != task.remote_task_id:
            raise FleetValidationError("unauthenticated or mismatched remote response")
        expected_input = f"sha256:{canonical_digest(task.input_digests)}"
        expected_output = (
            None
            if result.state != RemoteTaskState.SUCCEEDED
            else f"sha256:{canonical_digest(result.structured_payload)}"
        )
        if result.input_digest != expected_input or result.output_digest != expected_output:
            raise FleetValidationError("remote result integrity mismatch")
        return result

    def cancel(
        self, task: GovernedRemoteTask, *, authenticated_identity_ref: str
    ) -> RemoteTaskState:
        self.registry.authenticate(task.node_id, authenticated_identity_ref)
        adapter = self.adapters.get(task.node_id)
        if adapter is None:
            return RemoteTaskState.COMPLETION_UNKNOWN
        return adapter.cancel(task.remote_task_id, task.cancellation_token_id)

    def reconcile(self, task: GovernedRemoteTask) -> GovernedRemoteResult | None:
        adapter = self.adapters.get(task.node_id)
        return None if adapter is None else adapter.query(task.remote_task_id)


def fleet_evidence(
    event_type: str,
    subject_id: str,
    *,
    node_id: str | None,
    input_value: object,
    output_value: object | None,
    state: str,
    created_at: str,
    failure: str | None = None,
    details: Mapping[str, str] | None = None,
) -> FleetEvidence:
    input_digest = f"sha256:{canonical_digest(input_value)}"
    output_digest = None if output_value is None else f"sha256:{canonical_digest(output_value)}"
    values = {
        "event": event_type,
        "subject": subject_id,
        "node": node_id,
        "input": input_digest,
        "output": output_digest,
        "state": state,
        "failure": failure,
        "at": created_at,
    }
    return FleetEvidence(
        f"sha256:{canonical_digest(values)}",
        event_type,
        subject_id,
        node_id,
        input_digest,
        output_digest,
        state,
        failure,
        created_at,
        tuple(sorted((details or {}).items())),
    )


class FleetExecutionCoordinator:
    """Routes and invokes one already-validated orchestration step."""

    def __init__(
        self,
        router: GovernedFleetRouter,
        transport: GovernedFleetTransport,
        store: DurableFleetStore,
    ) -> None:
        self.router = router
        self.transport = transport
        self.store = store

    def execute(
        self,
        request: FleetRoutingRequest,
        health: Mapping[str, FleetNodeHealth],
        *,
        completed_at: str,
    ) -> tuple[FleetRoutingDecision, GovernedRemoteResult | None]:
        decision = self.router.route(request, health, decided_at=completed_at)
        self.store.append(
            fleet_evidence(
                "routing_decision",
                decision.fleet_decision_id,
                node_id=decision.selected_node_id,
                input_value=asdict(request),
                output_value=asdict(decision),
                state="selected" if decision.selected_node_id else "no_route",
                created_at=completed_at,
            )
        )
        if decision.selected_node_id is None:
            return decision, None
        registration = next(
            item
            for item in self.router.registry.registrations
            if item.identity.node_id == decision.selected_node_id
        )
        task = GovernedRemoteTask(
            f"remote-task-{canonical_digest({'request': request.fleet_request_id, 'node': decision.selected_node_id})[:64]}",
            request.fleet_request_id,
            request.orchestration_id,
            request.step_id,
            decision.selected_node_id,
            WorkerTaskType.RESEARCH_PREPARATION,
            request.required_capability,
            request.input_digests,
            "sigil.ai.output.remote-specialist.v1",
            request.maximum_duration_ms,
            request.maximum_memory_class,
            request.minimum_cpu_class,
            registration.maximum_input_chars,
            registration.maximum_output_chars,
            request.privacy_requirement,
            request.minimum_trust_tier,
            f"cancel-{canonical_digest(request.fleet_request_id)[:64]}",
            request.requested_at,
        )
        if any(
            item.event_type == "task_dispatch" and item.subject_id == task.remote_task_id
            for item in self.store.read()
        ):
            raise FleetConflictError("remote task replay rejected by durable evidence")
        self.store.append(
            fleet_evidence(
                "task_dispatch",
                task.remote_task_id,
                node_id=task.node_id,
                input_value=asdict(task),
                output_value=None,
                state=RemoteTaskState.NOT_STARTED.value,
                created_at=completed_at,
            )
        )
        try:
            result = self.transport.dispatch(
                task, authenticated_identity_ref=registration.identity.authenticated_identity_ref
            )
        except (TimeoutError, ConnectionError):
            self.store.append(
                fleet_evidence(
                    "completion_ambiguous",
                    task.remote_task_id,
                    node_id=task.node_id,
                    input_value=task.remote_task_id,
                    output_value=None,
                    state=RemoteTaskState.COMPLETION_UNKNOWN.value,
                    created_at=completed_at,
                    failure="transport_ambiguous",
                )
            )
            reconciled = self.transport.reconcile(task)
            return decision, reconciled
        self.store.append(
            fleet_evidence(
                "task_result",
                result.remote_result_id,
                node_id=result.node_id,
                input_value=result.input_digest,
                output_value=result.output_digest,
                state=result.state.value,
                created_at=completed_at,
                failure=result.failure_classification,
            )
        )
        if (
            result.state == RemoteTaskState.FAILED
            and result.failure_classification
            in {
                "provider_timeout",
                "provider_unavailable",
                "worker_unavailable",
                "recoverable_communication_failure",
            }
            and request.maximum_retries == 1
            and request.fallback_permission
        ):
            failover_request = replace(
                request,
                fleet_request_id=f"fleet-failover-{canonical_digest({'request': request.fleet_request_id, 'node': result.node_id})[:64]}",
                excluded_node_ids=(*request.excluded_node_ids, result.node_id),
                maximum_retries=0,
            )
            self.store.append(
                fleet_evidence(
                    "failover",
                    failover_request.fleet_request_id,
                    node_id=result.node_id,
                    input_value=request.fleet_request_id,
                    output_value=failover_request.fleet_request_id,
                    state="retry_next_eligible_node",
                    created_at=completed_at,
                    failure=result.failure_classification,
                )
            )
            return self.execute(failover_request, health, completed_at=completed_at)
        return decision, result

    def cancel(
        self, task: GovernedRemoteTask, *, authenticated_identity_ref: str, cancelled_at: str
    ) -> RemoteTaskState:
        self.store.append(
            fleet_evidence(
                "cancellation_requested",
                task.remote_task_id,
                node_id=task.node_id,
                input_value=task.cancellation_token_id,
                output_value=None,
                state=RemoteTaskState.CANCELLATION_REQUESTED.value,
                created_at=cancelled_at,
            )
        )
        state = self.transport.cancel(task, authenticated_identity_ref=authenticated_identity_ref)
        self.store.append(
            fleet_evidence(
                "cancellation_reconciled",
                task.remote_task_id,
                node_id=task.node_id,
                input_value=task.cancellation_token_id,
                output_value=state.value,
                state=state.value,
                created_at=cancelled_at,
            )
        )
        return state


class FleetSpecialistStepExecutor:
    """Phase 8 adapter: placement only, never planning or plan expansion."""

    def __init__(
        self,
        coordinator: FleetExecutionCoordinator,
        health: Mapping[str, FleetNodeHealth],
        request_factory,
    ) -> None:
        self.coordinator = coordinator
        self.health = health
        self.request_factory = request_factory

    def execute(
        self,
        step: GovernedOrchestrationStep,
        request: GovernedOrchestrationRequest,
        *,
        attempt: int,
        completed_at: str,
    ) -> GovernedStepResult:
        fleet_request = self.request_factory(step, request, attempt)
        _decision, remote = self.coordinator.execute(
            fleet_request, self.health, completed_at=completed_at
        )
        succeeded = remote is not None and remote.state == RemoteTaskState.SUCCEEDED
        return GovernedStepResult(
            f"fleet-step-result-{canonical_digest({'step': step.step_id, 'attempt': attempt})[:64]}",
            step.step_id,
            OrchestrationStepStatus.SUCCEEDED if succeeded else OrchestrationStepStatus.FAILED,
            None,
            () if remote is None else (remote.evidence_identity,),
            ()
            if remote is None
            else tuple(value for key, value in remote.structured_payload if key == "finding"),
            (),
            (),
            () if succeeded else (step.capability.value,),
            ("Fleet placement is advisory and carries no execution authority.",),
            None,
            "unknown",
            None if succeeded else "fleet_unavailable",
            remote is None or remote.state == RemoteTaskState.COMPLETION_UNKNOWN,
            False,
            attempt,
            completed_at,
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
