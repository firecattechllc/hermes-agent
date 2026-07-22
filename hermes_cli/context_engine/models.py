"""Domain models for Hermes Shared Engineering Context.

These are the canonical in-memory representations. All timestamps are UTC
Unix integers (seconds since epoch) for machine readability; display
conversions happen at the rendering layer.

Schema versioning: every model carries a ``schema_version`` field. Unknown or
future schema versions are rejected at load time — the store fail-closes rather
than silently interpreting new schemas incorrectly.
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


# ── Constants ────────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

_RECORD_TYPES = frozenset({
    "objective",
    "roadmap_item",
    "architecture_decision",
    "engineering_lesson",
    "known_risk",
    "blocker",
    "project_fact",
    "operating_constraint",
})

_RECORD_STATUSES = frozenset({
    "active",
    "resolved",
    "deprecated",
    "invalidated",
})

_PROJECT_STATUSES = frozenset({
    "active",
    "archived",
})

_LAUNCH_STAGES = frozenset({
    "planning",
    "implementation",
    "validation",
    "review",
    "promotion",
    "complete",
    "failed",
})

_LAUNCH_STATUSES = frozenset({
    "pending",
    "running",
    "complete",
    "failed",
    "cancelled",
})

_EVENT_TYPES = frozenset({
    "project_registered",
    "project_updated",
    "project_archived",
    "record_created",
    "record_updated",
    "record_superseded",
    "record_status_changed",
    "launch_started",
    "launch_updated",
    "launch_completed",
})


# ── Shared validators ───────────────────────────────────────────────────────

def _utc_now() -> int:
    return int(time.time())


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    return version


# ── Enums ────────────────────────────────────────────────────────────────────

class RecordType(str, Enum):
    OBJECTIVE = "objective"
    ROADMAP_ITEM = "roadmap_item"
    ARCHITECTURE_DECISION = "architecture_decision"
    ENGINEERING_LESSON = "engineering_lesson"
    KNOWN_RISK = "known_risk"
    BLOCKER = "blocker"
    PROJECT_FACT = "project_fact"
    OPERATING_CONSTRAINT = "operating_constraint"


class RecordStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    DEPRECATED = "deprecated"
    INVALIDATED = "invalidated"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class LaunchStage(str, Enum):
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    VALIDATION = "validation"
    REVIEW = "review"
    PROMOTION = "promotion"
    COMPLETE = "complete"
    FAILED = "failed"


class LaunchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Source reference ─────────────────────────────────────────────────────────

class SourceReference(BaseModel):
    """A provenance link to an external artifact (file, URL, commit, etc.)."""
    source_type: str = Field(..., min_length=1, max_length=64)
    source_identifier: str = Field(..., min_length=1, max_length=512)
    content_hash: Optional[str] = Field(None, max_length=128)
    captured_at: int = Field(default_factory=_utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_at")
    @classmethod
    def _check_utc(cls, v: int) -> int:
        if v < 0:
            raise ValueError("captured_at must be a non-negative Unix timestamp")
        return v


# ── Project ─────────────────────────────────────────────────────────────────

class Project(BaseModel):
    """A registered project that owns engineering context records."""
    project_id: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=256)
    repository_identity: Optional[str] = Field(None, max_length=512)
    local_path: Optional[str] = Field(None, max_length=1024)
    default_branch: Optional[str] = Field(None, max_length=128)
    status: ProjectStatus = Field(default=ProjectStatus.ACTIVE)
    created_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    def model_post_init(self, __context: Any) -> None:
        if self.updated_at < self.created_at:
            self.updated_at = self.created_at


# ── Context Record ───────────────────────────────────────────────────────────

class ContextRecord(BaseModel):
    """A project-scoped piece of engineering context."""
    record_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    record_type: RecordType
    title: str = Field(..., min_length=1, max_length=512)
    body: Optional[str] = Field(None, max_length=65536)
    structured_payload: Optional[Dict[str, Any]] = Field(None)
    status: RecordStatus = Field(default=RecordStatus.ACTIVE)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)
    source_refs: List[SourceReference] = Field(default_factory=list)
    supersedes: List[str] = Field(default_factory=list)
    related: List[str] = Field(default_factory=list)
    created_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    created_by: Optional[str] = Field(None, max_length=256)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @field_validator("tags")
    @classmethod
    def _dedupe_tags(cls, v: List[str]) -> List[str]:
        seen: set[str] = set()
        out: list[str] = []
        for t in v:
            t = t.strip().lower()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def model_post_init(self, __context: Any) -> None:
        if self.updated_at < self.created_at:
            self.updated_at = self.created_at


# ── Launch Context ───────────────────────────────────────────────────────────

class LaunchContext(BaseModel):
    """A structured launch/execution record bridging Foreman state."""
    launch_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    backlog_id: Optional[str] = Field(None, max_length=128)
    stage: LaunchStage = Field(default=LaunchStage.PLANNING)
    selected_agents: List[str] = Field(default_factory=list)
    status: LaunchStatus = Field(default=LaunchStatus.PENDING)
    evidence_refs: List[str] = Field(default_factory=list)
    commits: List[str] = Field(default_factory=list)
    branches: List[str] = Field(default_factory=list)
    pull_request_urls: List[str] = Field(default_factory=list)
    promotion_state: Optional[str] = Field(None, max_length=64)
    failure_reason: Optional[str] = Field(None, max_length=2048)
    started_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    completed_at: Optional[int] = Field(None)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── Context Event (audit journal entry) ─────────────────────────────────────

class ContextEvent(BaseModel):
    """An immutable, append-only audit journal entry.

    Every mutation to the store produces exactly one event. The full state of
    the store is deterministically recoverable by replaying the event journal.
    """
    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: str = Field(...)
    project_id: str = Field(..., min_length=1, max_length=128)
    actor: Optional[str] = Field(None, max_length=256)
    timestamp: int = Field(default_factory=_utc_now)
    correlation_id: Optional[str] = Field(None, max_length=128)
    causation_id: Optional[str] = Field(None, max_length=128)
    previous_state_ref: Optional[str] = Field(None, max_length=256)
    resulting_state_ref: Optional[str] = Field(None, max_length=256)
    payload: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("event_type")
    @classmethod
    def _check_event_type(cls, v: str) -> str:
        if v not in _EVENT_TYPES:
            raise ValueError(f"unknown event_type: {v!r}")
        return v

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)


# ── Snapshot ────────────────────────────────────────────────────────────────

class ContextSnapshot(BaseModel):
    """A deterministic point-in-time projection of active context.

    Built by replaying the event journal. The snapshot version is derived from
    the journal length so any two snapshots with the same version are identical.
    """
    version: int = Field(..., ge=0)
    generated_at: int = Field(default_factory=_utc_now)
    generated_by: Optional[str] = Field(None, max_length=256)
    project_id: str = Field(..., min_length=1, max_length=128)
    project: Optional[Project] = None
    records: List[ContextRecord] = Field(default_factory=list)
    launches: List[LaunchContext] = Field(default_factory=list)
    event_count: int = Field(default=0)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    def integrity_hash(self) -> str:
        """SHA-256 of canonical JSON (sorted keys, no indent) over snapshot content."""
        canonical = json.dumps(
            self.model_dump(
                exclude={"generated_at", "generated_by"},
                mode="json",
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# ── ID helpers ───────────────────────────────────────────────────────────────

def new_project_id() -> str:
    return "proj_" + secrets.token_hex(4)


def new_record_id() -> str:
    return "rec_" + secrets.token_hex(4)


def new_launch_id() -> str:
    return "laun_" + secrets.token_hex(4)


def new_event_id() -> str:
    return "evt_" + secrets.token_hex(8)


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


def parse_record_type(s: str) -> RecordType:
    return RecordType(s.strip().lower())


def parse_record_status(s: str) -> RecordStatus:
    return RecordStatus(s.strip().lower())


def parse_project_status(s: str) -> ProjectStatus:
    return ProjectStatus(s.strip().lower())


def parse_launch_stage(s: str) -> LaunchStage:
    return LaunchStage(s.strip().lower())


def parse_launch_status(s: str) -> LaunchStatus:
    return LaunchStatus(s.strip().lower())
