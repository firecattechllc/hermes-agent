"""Domain models for Hermes Mission Control telemetry and visibility.

These are the canonical in-memory representations. All timestamps are UTC
Unix integers (seconds since epoch) for machine readability.

Schema versioning: every model carries a ``schema_version`` field. Unknown or
future schema versions are rejected at load time — the store fail-closes rather
than silently interpreting new schemas incorrectly.

Mission Control is read-only from the UI perspective. The store accepts
incoming TelemetryEvent records and deterministically projects them into a
MissionControlSnapshot. Browser/UI code reads the snapshot through the service
layer and must never directly inspect runtime files.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Schema versioning ────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


def _utc_now() -> int:
    return int(time.time())


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    return version


# ── Allowed value sets ───────────────────────────────────────────────────────

_AGENT_STATES = frozenset({
    "idle",
    "thinking",
    "running_tools",
    "waiting_approval",
    "blocked",
    "error",
    "complete",
})

_BACKLOG_STATES = frozenset({
    "backlog",
    "in_progress",
    "blocked",
    "done",
    "cancelled",
})

_APPROVAL_STATES = frozenset({
    "pending",
    "approved",
    "rejected",
    "expired",
})

_EVIDENCE_STATES = frozenset({
    "pending",
    "collected",
    "verified",
    "failed",
})

_PROMOTION_STATES = frozenset({
    "not_started",
    "in_progress",
    "approved",
    "rejected",
    "deployed",
})

_TELEMETRY_SEVERITIES = frozenset({
    "debug",
    "info",
    "warning",
    "error",
    "critical",
})

_TELEMETRY_EVENT_TYPES = frozenset({
    # Agent lifecycle
    "agent_started",
    "agent_thinking",
    "agent_tools_started",
    "agent_tools_completed",
    "agent_waiting_approval",
    "agent_approved",
    "agent_rejected",
    "agent_blocked",
    "agent_error",
    "agent_complete",
    # Backlog lifecycle
    "backlog_item_created",
    "backlog_item_started",
    "backlog_item_blocked",
    "backlog_item_done",
    "backlog_item_cancelled",
    "backlog_item_updated",
    # Approval lifecycle
    "approval_requested",
    "approval_granted",
    "approval_denied",
    "approval_expired",
    # Evidence lifecycle
    "evidence_requested",
    "evidence_collected",
    "evidence_verified",
    "evidence_failed",
    # Promotion lifecycle
    "promotion_requested",
    "promotion_started",
    "promotion_approved",
    "promotion_rejected",
    "promotion_deployed",
    # Visibility / observability
    "snapshot_generated",
    "context_ingested",
    "context_launch_imported",
})


# ── Enums ────────────────────────────────────────────────────────────────────

class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    RUNNING_TOOLS = "running_tools"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    ERROR = "error"
    COMPLETE = "complete"


class BacklogItemState(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class EvidenceState(str, Enum):
    PENDING = "pending"
    COLLECTED = "collected"
    VERIFIED = "verified"
    FAILED = "failed"


class PromotionState(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPLOYED = "deployed"


class TelemetrySeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ── TelemetryEvent (the append-only journal record) ─────────────────────────

class TelemetryEvent(BaseModel):
    """An immutable, append-only telemetry journal entry.

    Every state transition or observable action in the agent lifecycle produces
    exactly one TelemetryEvent. The full operational state is deterministically
    recoverable by replaying the event journal in sequence order.

    Required provenance fields (``event_id``, ``project_id``, ``timestamp``,
    ``sequence``) ensure stable ordering and traceability even when events
    arrive out-of-band (e.g., from context engine ingestion).
    """
    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: str = Field(...)
    project_id: str = Field(..., min_length=1, max_length=128)
    launch_id: Optional[str] = Field(None, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    backlog_id: Optional[str] = Field(None, max_length=128)
    agent_id: Optional[str] = Field(None, max_length=256)
    timestamp: int = Field(default_factory=_utc_now)
    sequence: int = Field(default=0, ge=0)
    severity: str = Field(default="info")
    correlation_id: Optional[str] = Field(None, max_length=128)
    causation_id: Optional[str] = Field(None, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("event_type")
    @classmethod
    def _check_event_type(cls, v: str) -> str:
        if v not in _TELEMETRY_EVENT_TYPES:
            raise ValueError(f"unknown telemetry event_type: {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def _check_severity(cls, v: str) -> str:
        if v not in _TELEMETRY_SEVERITIES:
            raise ValueError(f"unknown telemetry severity: {v!r}")
        return v

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, v: int) -> int:
        if v < 0:
            raise ValueError("timestamp must be a non-negative Unix timestamp")
        return v

    def stable_sort_key(self) -> tuple:
        """Return a tuple suitable for deterministic, stable ordering.

        Ordering is: timestamp → sequence → event_id.
        """
        return (self.timestamp, self.sequence, self.event_id)

    def source_provenance(self) -> Dict[str, str]:
        """Return the explicit provenance fields for this event."""
        return {
            "event_id": self.event_id,
            "project_id": self.project_id,
            "launch_id": self.launch_id or "",
            "task_id": self.task_id or "",
            "backlog_id": self.backlog_id or "",
            "agent_id": self.agent_id or "",
            "causation_id": self.causation_id or "",
        }


# ── Agent state snapshot ─────────────────────────────────────────────────────

class AgentStateSnapshot(BaseModel):
    """Point-in-time snapshot of an agent's observable state."""
    agent_id: str = Field(..., min_length=1, max_length=256)
    project_id: str = Field(..., min_length=1, max_length=128)
    launch_id: Optional[str] = Field(None, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    state: AgentState = Field(default=AgentState.IDLE)
    last_event_id: Optional[str] = Field(None, max_length=128)
    last_event_type: Optional[str] = Field(None, max_length=128)
    last_event_timestamp: Optional[int] = Field(None)
    updated_at: int = Field(default_factory=_utc_now)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── Backlog item state snapshot ─────────────────────────────────────────────

class BacklogItemStateSnapshot(BaseModel):
    """Point-in-time snapshot of a backlog item's state."""
    backlog_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    launch_id: Optional[str] = Field(None, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    state: BacklogItemState = Field(default=BacklogItemState.BACKLOG)
    title: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = Field(None, max_length=4096)
    created_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── Approval state snapshot ─────────────────────────────────────────────────

class ApprovalStateSnapshot(BaseModel):
    """Point-in-time snapshot of an approval's state."""
    approval_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    launch_id: Optional[str] = Field(None, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    backlog_id: Optional[str] = Field(None, max_length=128)
    state: ApprovalState = Field(default=ApprovalState.PENDING)
    requested_by: Optional[str] = Field(None, max_length=256)
    requested_at: Optional[int] = Field(None)
    resolved_by: Optional[str] = Field(None, max_length=256)
    resolved_at: Optional[int] = Field(None)
    summary: Optional[str] = Field(None, max_length=2048)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── Evidence state snapshot ───────────────────────────────────────────────────

class EvidenceStateSnapshot(BaseModel):
    """Point-in-time snapshot of evidence collection state."""
    evidence_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    launch_id: Optional[str] = Field(None, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    backlog_id: Optional[str] = Field(None, max_length=128)
    state: EvidenceState = Field(default=EvidenceState.PENDING)
    source_path: Optional[str] = Field(None, max_length=1024)
    content_hash: Optional[str] = Field(None, max_length=128)
    collected_at: Optional[int] = Field(None)
    verified_at: Optional[int] = Field(None)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── Promotion state snapshot ─────────────────────────────────────────────────

class PromotionStateSnapshot(BaseModel):
    """Point-in-time snapshot of a promotion's state."""
    promotion_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    launch_id: Optional[str] = Field(None, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    backlog_id: Optional[str] = Field(None, max_length=128)
    state: PromotionState = Field(default=PromotionState.NOT_STARTED)
    requested_by: Optional[str] = Field(None, max_length=256)
    requested_at: Optional[int] = Field(None)
    approved_by: Optional[str] = Field(None, max_length=256)
    approved_at: Optional[int] = Field(None)
    deployed_at: Optional[int] = Field(None)
    target_ref: Optional[str] = Field(None, max_length=256)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── MissionControlSnapshot ────────────────────────────────────────────────────

class MissionControlSnapshot(BaseModel):
    """A deterministic point-in-time projection of mission control state.

    Built by replaying the telemetry event journal. The snapshot version is
    derived from the event count so any two snapshots with the same version
    and the same source journal are identical.
    """
    version: int = Field(..., ge=0)
    generated_at: int = Field(default_factory=_utc_now)
    generated_by: Optional[str] = Field(None, max_length=256)
    project_id: str = Field(..., min_length=1, max_length=128)
    event_count: int = Field(default=0)
    events: List[TelemetryEvent] = Field(default_factory=list)
    agent_states: List[AgentStateSnapshot] = Field(default_factory=list)
    backlog_states: List[BacklogItemStateSnapshot] = Field(default_factory=list)
    approval_states: List[ApprovalStateSnapshot] = Field(default_factory=list)
    evidence_states: List[EvidenceStateSnapshot] = Field(default_factory=list)
    promotion_states: List[PromotionStateSnapshot] = Field(default_factory=list)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    def integrity_hash(self) -> str:
        """SHA-256 of canonical JSON (sorted keys, no indent) over snapshot content.

        Excludes ``integrity_hash`` itself and the ``version`` field (which
        depends on event count and would make the hash non-reproducible across
        builds). The caller is responsible for tracking version separately.
        """
        data = self.model_dump(exclude={"integrity_hash", "version"}, mode="json")
        canonical = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# ── ID helpers ───────────────────────────────────────────────────────────────

def new_telemetry_event_id() -> str:
    return "tevt_" + secrets.token_hex(8)


def new_agent_id() -> str:
    return "agnt_" + secrets.token_hex(4)


def new_backlog_id() -> str:
    return "bkit_" + secrets.token_hex(4)


def new_approval_id() -> str:
    return "appr_" + secrets.token_hex(4)


def new_evidence_id() -> str:
    return "evdn_" + secrets.token_hex(4)


def new_promotion_id() -> str:
    return "promo_" + secrets.token_hex(4)


# ── Serialisation helpers ────────────────────────────────────────────────────

def utc_timestamp(dt: Optional[datetime] = None) -> int:
    """Return a UTC Unix timestamp (seconds)."""
    if dt is None:
        return _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def format_timestamp(ts: int) -> str:
    """Human-readable UTC ISO-8601 string from Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_agent_state(s: str) -> AgentState:
    return AgentState(s.strip().lower())


def parse_backlog_state(s: str) -> BacklogItemState:
    return BacklogItemState(s.strip().lower())


def parse_approval_state(s: str) -> ApprovalState:
    return ApprovalState(s.strip().lower())


def parse_evidence_state(s: str) -> EvidenceState:
    return EvidenceState(s.strip().lower())


def parse_promotion_state(s: str) -> PromotionState:
    return PromotionState(s.strip().lower())


def parse_telemetry_severity(s: str) -> TelemetrySeverity:
    return TelemetrySeverity(s.strip().lower())
