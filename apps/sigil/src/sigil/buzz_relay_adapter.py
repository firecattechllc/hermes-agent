"""Disabled-by-default governed Buzz Relay adapter.

Stage 5 models Buzz channels, projects, actors, threads, signed relay events,
approval references, Git/workflow events, evidence links, replay protection,
and worker-job lifecycle projection.

This module performs no network requests, authentication, message sending,
relay subscription, approval, job dispatch, credential resolution, shell
execution, filesystem access, installation, activation, or financial action.
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

BUZZ_RELAY_ADAPTER_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[a-z0-9._-]+:[A-Za-z0-9_-]{32,512}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)
_REPOSITORY = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
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


class BuzzRelayValidationError(ValueError):
    """Buzz Relay adapter input failed closed."""


class BuzzActorKind(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


class BuzzEventKind(str, Enum):
    MESSAGE = "message"
    THREAD_REPLY = "thread_reply"
    APPROVAL_REFERENCE = "approval_reference"
    GIT_EVENT = "git_event"
    WORKFLOW_EVENT = "workflow_event"
    JOB_EVENT = "job_event"
    EVIDENCE_EVENT = "evidence_event"
    STATUS_EVENT = "status_event"


class BuzzDeliveryState(str, Enum):
    DISABLED = "disabled"
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    STALE = "stale"
    INVALID = "invalid"
    INCOMPATIBLE = "incompatible"


class BuzzWorkState(str, Enum):
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


_JOB_STATE_PROJECTION: dict[JobState, BuzzWorkState] = {
    JobState.PROPOSED: BuzzWorkState.PROPOSED,
    JobState.ADMITTED: BuzzWorkState.ADMITTED,
    JobState.REJECTED: BuzzWorkState.REJECTED,
    JobState.QUEUED: BuzzWorkState.QUEUED,
    JobState.RUNNING: BuzzWorkState.RUNNING,
    JobState.CANCELLATION_REQUESTED: BuzzWorkState.CANCELLATION_REQUESTED,
    JobState.CANCELLED: BuzzWorkState.CANCELLED,
    JobState.SUCCEEDED: BuzzWorkState.SUCCEEDED,
    JobState.FAILED: BuzzWorkState.FAILED,
    JobState.COMPLETION_UNKNOWN: BuzzWorkState.COMPLETION_UNKNOWN,
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise BuzzRelayValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise BuzzRelayValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise BuzzRelayValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise BuzzRelayValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise BuzzRelayValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise BuzzRelayValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise BuzzRelayValidationError(
            f"{label} must be a repository-relative reference"
        )


@dataclass(frozen=True, slots=True)
class BuzzRelayConfig:
    integration_id: str = "buzz-relay"
    enabled: bool = False
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    schema_version: int = BUZZ_RELAY_ADAPTER_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != BUZZ_RELAY_ADAPTER_SCHEMA_VERSION:
            raise BuzzRelayValidationError(
                "unsupported Buzz Relay adapter schema"
            )
        _require_identifier(self.integration_id, "Buzz integration ID")

        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise BuzzRelayValidationError(
                "incompatible worker contract schema"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Buzz Relay configuration")

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_send(self) -> bool:
        return False

    @property
    def can_subscribe(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_approve(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class BuzzSpaceRef:
    workspace_id: str
    project_id: str
    channel_id: str
    workspace_name: str
    project_name: str
    channel_name: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.workspace_id, "workspace ID"),
            (self.project_id, "project ID"),
            (self.channel_id, "channel ID"),
        ):
            _require_identifier(value, label)

        for value, label in (
            (self.workspace_name, "workspace name"),
            (self.project_name, "project name"),
            (self.channel_name, "channel name"),
        ):
            if not value.strip():
                raise BuzzRelayValidationError(f"{label} is required")

        _validate_sanitized(asdict(self), "Buzz space reference")


@dataclass(frozen=True, slots=True)
class BuzzActorRef:
    actor_id: str
    display_name: str
    kind: BuzzActorKind
    organization_identity: str

    def __post_init__(self) -> None:
        _require_identifier(self.actor_id, "actor ID")

        if not self.display_name.strip():
            raise BuzzRelayValidationError("actor display name is required")
        if not self.organization_identity.strip():
            raise BuzzRelayValidationError(
                "actor organization identity is required"
            )
        if not isinstance(self.kind, BuzzActorKind):
            raise BuzzRelayValidationError("unknown Buzz actor kind")

        _validate_sanitized(asdict(self), "Buzz actor reference")


@dataclass(frozen=True, slots=True)
class BuzzThreadRef:
    message_id: str
    thread_id: str | None
    parent_message_id: str | None

    def __post_init__(self) -> None:
        _require_identifier(self.message_id, "message ID")

        if self.thread_id is not None:
            _require_identifier(self.thread_id, "thread ID")
        if self.parent_message_id is not None:
            _require_identifier(self.parent_message_id, "parent message ID")

        if self.parent_message_id is not None and self.thread_id is None:
            raise BuzzRelayValidationError(
                "parent message requires a thread identity"
            )


@dataclass(frozen=True, slots=True)
class BuzzApprovalRef:
    approval_id: str
    policy_revision: str
    approval_scope: tuple[str, ...]
    approved: bool
    evidence_digest: str

    def __post_init__(self) -> None:
        _require_identifier(self.approval_id, "approval ID")
        _require_digest(self.evidence_digest, "approval evidence digest")

        if not self.policy_revision.strip():
            raise BuzzRelayValidationError(
                "approval policy revision is required"
            )
        for scope in self.approval_scope:
            _require_identifier(scope, "approval scope")

        _validate_sanitized(asdict(self), "Buzz approval reference")


@dataclass(frozen=True, slots=True)
class BuzzGitRef:
    repository_identity: str
    revision: str
    event_name: str
    workflow_reference: str | None

    def __post_init__(self) -> None:
        if _REPOSITORY.fullmatch(self.repository_identity) is None:
            raise BuzzRelayValidationError(
                "malformed Git repository identity"
            )
        if _REVISION.fullmatch(self.revision) is None:
            raise BuzzRelayValidationError(
                "Git revision must be an immutable commit"
            )
        _require_identifier(self.event_name, "Git event name")

        if self.workflow_reference is not None:
            _require_relative_reference(
                self.workflow_reference,
                "workflow reference",
            )

        _validate_sanitized(asdict(self), "Buzz Git reference")


@dataclass(frozen=True, slots=True)
class BuzzEvidenceRef:
    evidence_id: str
    kind: str
    content_digest: str
    provenance: str
    reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence ID")
        _require_identifier(self.kind, "evidence kind")
        _require_digest(self.content_digest, "evidence digest")

        if not self.provenance.strip():
            raise BuzzRelayValidationError(
                "evidence provenance is required"
            )

        _validate_sanitized(asdict(self), "Buzz evidence reference")
        _require_relative_reference(self.reference, "evidence reference")


@dataclass(frozen=True, slots=True)
class BuzzRelayEvent:
    event_id: str
    sequence: int
    emitted_at: str
    kind: BuzzEventKind
    space: BuzzSpaceRef
    actor: BuzzActorRef
    thread: BuzzThreadRef
    correlation_id: str
    idempotency_key: str
    payload: Mapping[str, object]
    payload_digest: str
    previous_event_digest: str
    signature: str
    approval: BuzzApprovalRef | None = None
    git: BuzzGitRef | None = None
    evidence: tuple[BuzzEvidenceRef, ...] = ()
    schema_version: int = BUZZ_RELAY_ADAPTER_SCHEMA_VERSION
    event_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.event_digest and self.event_digest != expected:
            raise BuzzRelayValidationError(
                "Buzz relay event digest mismatch"
            )

        if not self.event_digest:
            object.__setattr__(self, "event_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload.pop("event_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != BUZZ_RELAY_ADAPTER_SCHEMA_VERSION:
            raise BuzzRelayValidationError(
                "unsupported Buzz relay event schema"
            )

        for value, label in (
            (self.event_id, "event ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
        ):
            _require_identifier(value, label)

        if self.sequence < 0:
            raise BuzzRelayValidationError(
                "relay event sequence cannot be negative"
            )

        _require_timestamp(self.emitted_at, "event emission time")
        _require_digest(self.payload_digest, "payload digest")
        _require_digest(
            self.previous_event_digest,
            "previous event digest",
        )

        expected_payload_digest = (
            f"sha256:{canonical_digest(dict(self.payload))}"
        )
        if self.payload_digest != expected_payload_digest:
            raise BuzzRelayValidationError(
                "Buzz relay payload digest mismatch"
            )

        if _SIGNATURE.fullmatch(self.signature) is None:
            raise BuzzRelayValidationError(
                "Buzz relay signature format is invalid"
            )

        if not isinstance(self.kind, BuzzEventKind):
            raise BuzzRelayValidationError("unknown Buzz event kind")

        if len({item.evidence_id for item in self.evidence}) != len(
            self.evidence
        ):
            raise BuzzRelayValidationError(
                "duplicate Buzz evidence identity"
            )

        if self.kind is BuzzEventKind.APPROVAL_REFERENCE:
            if self.approval is None:
                raise BuzzRelayValidationError(
                    "approval event requires an approval reference"
                )
        elif self.approval is not None:
            raise BuzzRelayValidationError(
                "approval reference is only valid for approval events"
            )

        if self.kind in {
            BuzzEventKind.GIT_EVENT,
            BuzzEventKind.WORKFLOW_EVENT,
        }:
            if self.git is None:
                raise BuzzRelayValidationError(
                    "Git or workflow event requires a Git reference"
                )
        elif self.git is not None:
            raise BuzzRelayValidationError(
                "Git reference is only valid for Git or workflow events"
            )

        self.authority.validate()
        _validate_sanitized(self.digest_payload(), "Buzz relay event")

    @property
    def can_execute(self) -> bool:
        return False

    @property
    def can_approve_work(self) -> bool:
        return False

    @property
    def can_send_message(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class BuzzReplayWindow:
    highest_sequence: int
    accepted_event_ids: tuple[str, ...]
    accepted_idempotency_keys: tuple[str, ...]
    last_event_digest: str

    def __post_init__(self) -> None:
        if self.highest_sequence < -1:
            raise BuzzRelayValidationError(
                "highest relay sequence is invalid"
            )

        for value in self.accepted_event_ids:
            _require_identifier(value, "accepted event ID")
        for value in self.accepted_idempotency_keys:
            _require_identifier(value, "accepted idempotency key")

        if len(set(self.accepted_event_ids)) != len(
            self.accepted_event_ids
        ):
            raise BuzzRelayValidationError(
                "duplicate accepted event identity"
            )
        if len(set(self.accepted_idempotency_keys)) != len(
            self.accepted_idempotency_keys
        ):
            raise BuzzRelayValidationError(
                "duplicate accepted idempotency key"
            )

        _require_digest(self.last_event_digest, "last event digest")


@dataclass(frozen=True, slots=True)
class BuzzDeliveryDecision:
    event_id: str
    state: BuzzDeliveryState
    accepted: bool
    reason: str
    event_digest: str
    next_window: BuzzReplayWindow
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "decision event ID")
        _require_digest(self.event_digest, "decision event digest")

        if not isinstance(self.state, BuzzDeliveryState):
            raise BuzzRelayValidationError(
                "unknown Buzz delivery state"
            )
        if not self.reason.strip():
            raise BuzzRelayValidationError(
                "Buzz delivery reason is required"
            )
        if self.accepted != (
            self.state is BuzzDeliveryState.ACCEPTED
        ):
            raise BuzzRelayValidationError(
                "Buzz delivery acceptance state is inconsistent"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Buzz delivery decision")


@dataclass(frozen=True, slots=True)
class BuzzJobProjection:
    job_id: str
    correlation_id: str
    idempotency_key: str
    state: BuzzWorkState
    worker_contract_digest: str
    worker_contract_schema: int
    created_at: str
    schema_version: int = BUZZ_RELAY_ADAPTER_SCHEMA_VERSION
    projection_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        for value, label in (
            (self.job_id, "job ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
        ):
            _require_identifier(value, label)

        if not isinstance(self.state, BuzzWorkState):
            raise BuzzRelayValidationError(
                "unknown Buzz work state"
            )

        _require_digest(
            self.worker_contract_digest,
            "worker contract digest",
        )
        _require_timestamp(self.created_at, "job creation time")

        if self.worker_contract_schema != WORKER_CONTRACT_SCHEMA_VERSION:
            raise BuzzRelayValidationError(
                "Buzz projection worker schema is incompatible"
            )
        if self.schema_version != BUZZ_RELAY_ADAPTER_SCHEMA_VERSION:
            raise BuzzRelayValidationError(
                "unsupported Buzz job projection schema"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Buzz job projection",
        )

        expected = self.expected_digest()

        if self.projection_digest and self.projection_digest != expected:
            raise BuzzRelayValidationError(
                "Buzz job projection digest mismatch"
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


def validate_buzz_registry_entry(
    config: BuzzRelayConfig,
    entry: IntegrationRegistryEntry,
) -> None:
    """Validate that the Stage 1 registry describes Buzz safely."""

    if entry.integration_id != config.integration_id:
        raise BuzzRelayValidationError(
            "Buzz registry identity mismatch"
        )
    if entry.category is not IntegrationCategory.COLLABORATION:
        raise BuzzRelayValidationError(
            "Buzz must be registered as a collaboration integration"
        )
    if entry.lifecycle_state in {
        LifecycleState.REJECTED,
        LifecycleState.DEPRECATED,
        LifecycleState.QUARANTINED,
    }:
        raise BuzzRelayValidationError(
            "Buzz registry lifecycle is not eligible"
        )

    entry.authority.validate()

    if entry.can_activate:
        raise BuzzRelayValidationError(
            "Buzz registry entry unexpectedly permits activation"
        )


def initial_replay_window() -> BuzzReplayWindow:
    return BuzzReplayWindow(
        highest_sequence=-1,
        accepted_event_ids=(),
        accepted_idempotency_keys=(),
        last_event_digest="sha256:" + "0" * 64,
    )


def evaluate_relay_event(
    config: BuzzRelayConfig,
    event: BuzzRelayEvent,
    window: BuzzReplayWindow,
    *,
    age_seconds: int,
    stale_after_seconds: int = 300,
) -> BuzzDeliveryDecision:
    """Evaluate one injected signed event without contacting Buzz."""

    if not 1 <= stale_after_seconds <= 86400:
        raise BuzzRelayValidationError(
            "relay staleness threshold is outside bounds"
        )
    if age_seconds < 0:
        raise BuzzRelayValidationError(
            "Buzz relay event cannot originate in the future"
        )

    if not config.enabled:
        return BuzzDeliveryDecision(
            event_id=event.event_id,
            state=BuzzDeliveryState.DISABLED,
            accepted=False,
            reason="Buzz Relay adapter is disabled by policy.",
            event_digest=event.event_digest,
            next_window=window,
        )

    if event.event_id in window.accepted_event_ids:
        return BuzzDeliveryDecision(
            event_id=event.event_id,
            state=BuzzDeliveryState.DUPLICATE,
            accepted=False,
            reason="Buzz relay event identity was already accepted.",
            event_digest=event.event_digest,
            next_window=window,
        )

    if event.idempotency_key in window.accepted_idempotency_keys:
        return BuzzDeliveryDecision(
            event_id=event.event_id,
            state=BuzzDeliveryState.DUPLICATE,
            accepted=False,
            reason="Buzz relay idempotency key was already accepted.",
            event_digest=event.event_digest,
            next_window=window,
        )

    if event.sequence <= window.highest_sequence:
        return BuzzDeliveryDecision(
            event_id=event.event_id,
            state=BuzzDeliveryState.DUPLICATE,
            accepted=False,
            reason="Buzz relay sequence is not strictly increasing.",
            event_digest=event.event_digest,
            next_window=window,
        )

    if event.previous_event_digest != window.last_event_digest:
        raise BuzzRelayValidationError(
            "Buzz relay hash chain does not match replay window"
        )

    if age_seconds > stale_after_seconds:
        return BuzzDeliveryDecision(
            event_id=event.event_id,
            state=BuzzDeliveryState.STALE,
            accepted=False,
            reason="Buzz relay event exceeded the freshness window.",
            event_digest=event.event_digest,
            next_window=window,
        )

    next_window = BuzzReplayWindow(
        highest_sequence=event.sequence,
        accepted_event_ids=(
            *window.accepted_event_ids,
            event.event_id,
        ),
        accepted_idempotency_keys=(
            *window.accepted_idempotency_keys,
            event.idempotency_key,
        ),
        last_event_digest=event.event_digest,
    )

    return BuzzDeliveryDecision(
        event_id=event.event_id,
        state=BuzzDeliveryState.ACCEPTED,
        accepted=True,
        reason="Buzz relay event is current, signed, and replay-safe.",
        event_digest=event.event_digest,
        next_window=next_window,
    )


def project_worker_job(
    config: BuzzRelayConfig,
    job: GovernedWorkerJob,
) -> BuzzJobProjection:
    """Project one Stage 2 worker job into a descriptive Buzz work state."""

    if job.integration_id != config.integration_id:
        raise BuzzRelayValidationError(
            "worker job integration does not match Buzz Relay"
        )
    if job.schema_version != config.expected_worker_contract_schema:
        raise BuzzRelayValidationError(
            "worker job schema is incompatible with Buzz Relay"
        )

    job.authority.validate()

    return BuzzJobProjection(
        job_id=job.job_id,
        correlation_id=job.correlation_id,
        idempotency_key=job.idempotency_key,
        state=_JOB_STATE_PROJECTION[job.state],
        worker_contract_digest=job.contract_digest,
        worker_contract_schema=job.schema_version,
        created_at=job.created_at,
    )


def lifecycle_projection() -> Mapping[JobState, BuzzWorkState]:
    """Expose a copy of the deterministic worker-to-Buzz state map."""

    return dict(_JOB_STATE_PROJECTION)
