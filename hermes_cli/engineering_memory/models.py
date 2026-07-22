"""Domain models for Hermes Structured Engineering Memory.

Structured Engineering Memory preserves durable, project-scoped knowledge
learned through engineering work. It is distinct from:

- Shared Engineering Context, which represents current project context.
- Mission Control, which represents operational telemetry and projected state.

Memory enters as a candidate and must pass explicit governance before it can
be treated as verified knowledge. History is preserved through immutable
provenance, append-only lifecycle events, and supersession rather than
destructive replacement.

All timestamps are UTC Unix integers. Unknown schema versions fail closed.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


# ── Enums ────────────────────────────────────────────────────────────────────

class MemoryType(str, Enum):
    ARCHITECTURE_DECISION = "architecture_decision"
    IMPLEMENTATION_LESSON = "implementation_lesson"
    DEBUGGING_LESSON = "debugging_lesson"
    TEST_EVIDENCE = "test_evidence"
    PROJECT_CONVENTION = "project_convention"
    INVARIANT = "invariant"
    SECURITY_FINDING = "security_finding"
    DEPENDENCY_NOTE = "dependency_note"
    REJECTED_APPROACH = "rejected_approach"
    OPERATIONAL_LESSON = "operational_lesson"
    IMPROVEMENT_CANDIDATE = "improvement_candidate"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemorySourceType(str, Enum):
    HUMAN = "human"
    CONTEXT_RECORD = "context_record"
    CONTEXT_LAUNCH = "context_launch"
    MISSION_CONTROL_EVENT = "mission_control_event"
    MISSION_CONTROL_SNAPSHOT = "mission_control_snapshot"
    TOOL_EXECUTION = "tool_execution"
    TEST_RESULT = "test_result"
    APPROVAL = "approval"
    PROMOTION = "promotion"
    AGENT_OBSERVATION = "agent_observation"
    IMPORT = "import"


class MemoryEventType(str, Enum):
    CREATED = "memory_created"
    VERIFIED = "memory_verified"
    REJECTED = "memory_rejected"
    SUPERSEDED = "memory_superseded"
    ARCHIVED = "memory_archived"


# ── Immutable provenance ─────────────────────────────────────────────────────

class MemoryProvenance(BaseModel):
    """Immutable origin information for a memory record.

    Provenance is never rewritten during verification, rejection, archival, or
    supersession. Corrections create new records and link them explicitly.
    """

    model_config = ConfigDict(frozen=True)

    source_type: MemorySourceType
    source_ids: Tuple[str, ...] = Field(default_factory=tuple)
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)
    captured_at: int = Field(default_factory=_utc_now)
    captured_by: Optional[str] = Field(None, max_length=256)
    content_hash: Optional[str] = Field(None, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_ids", "evidence_refs")
    @classmethod
    def _normalise_references(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            normalised = value.strip()
            if normalised and normalised not in seen:
                seen.add(normalised)
                output.append(normalised)
        return tuple(output)

    @field_validator("captured_at")
    @classmethod
    def _check_timestamp(cls, value: int) -> int:
        if value < 0:
            raise ValueError("captured_at must be a non-negative Unix timestamp")
        return value


# ── Memory record ────────────────────────────────────────────────────────────

class MemoryRecord(BaseModel):
    """A durable, governed unit of project-scoped engineering knowledge."""

    memory_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    memory_type: MemoryType
    title: str = Field(..., min_length=1, max_length=512)
    summary: str = Field(..., min_length=1, max_length=4096)
    body: Optional[str] = Field(None, max_length=65536)
    structured_payload: Optional[Dict[str, Any]] = None

    status: MemoryStatus = Field(default=MemoryStatus.CANDIDATE)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    provenance: MemoryProvenance
    tags: List[str] = Field(default_factory=list)
    related_memory_ids: List[str] = Field(default_factory=list)

    created_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    created_by: Optional[str] = Field(None, max_length=256)
    reviewed_by: Optional[str] = Field(None, max_length=256)
    reviewed_at: Optional[int] = None
    review_note: Optional[str] = Field(None, max_length=4096)

    supersedes: List[str] = Field(default_factory=list)
    superseded_by: Optional[str] = Field(None, max_length=128)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator("created_at", "updated_at", "reviewed_at")
    @classmethod
    def _check_timestamps(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("timestamps must be non-negative Unix timestamps")
        return value

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, values: List[str]) -> List[str]:
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            normalised = value.strip().lower()
            if normalised and normalised not in seen:
                seen.add(normalised)
                output.append(normalised)
        return output

    @field_validator("related_memory_ids", "supersedes")
    @classmethod
    def _normalise_ids(cls, values: List[str]) -> List[str]:
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            normalised = value.strip()
            if normalised and normalised not in seen:
                seen.add(normalised)
                output.append(normalised)
        return output

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "MemoryRecord":
        if self.updated_at < self.created_at:
            self.updated_at = self.created_at

        if self.status == MemoryStatus.VERIFIED:
            if not self.reviewed_by:
                raise ValueError("verified memory requires reviewed_by")
            if self.reviewed_at is None:
                raise ValueError("verified memory requires reviewed_at")

        if self.status == MemoryStatus.REJECTED:
            if not self.reviewed_by:
                raise ValueError("rejected memory requires reviewed_by")
            if self.reviewed_at is None:
                raise ValueError("rejected memory requires reviewed_at")
            if not self.review_note:
                raise ValueError("rejected memory requires review_note")

        if self.status == MemoryStatus.SUPERSEDED and not self.superseded_by:
            raise ValueError("superseded memory requires superseded_by")

        if self.superseded_by == self.memory_id:
            raise ValueError("memory cannot supersede itself")

        if self.memory_id in self.supersedes:
            raise ValueError("memory cannot list itself in supersedes")

        return self

    def content_fingerprint(self) -> str:
        """Return a deterministic fingerprint for duplicate detection.

        Governance state, timestamps, reviewers, and record identity are
        excluded. Semantically identical candidate content therefore produces
        the same fingerprint regardless of when or by whom it was submitted.
        """
        canonical = json.dumps(
            {
                "project_id": self.project_id,
                "memory_type": self.memory_type.value,
                "title": self.title.strip(),
                "summary": self.summary.strip(),
                "body": self.body,
                "structured_payload": self.structured_payload,
                "source_type": self.provenance.source_type.value,
                "source_ids": list(self.provenance.source_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# ── Append-only lifecycle event ──────────────────────────────────────────────

class MemoryEvent(BaseModel):
    """Immutable lifecycle event for deterministic replay."""

    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: MemoryEventType
    project_id: str = Field(..., min_length=1, max_length=128)
    memory_id: str = Field(..., min_length=1, max_length=128)
    actor: Optional[str] = Field(None, max_length=256)
    timestamp: int = Field(default_factory=_utc_now)
    sequence: int = Field(default=0, ge=0)
    correlation_id: Optional[str] = Field(None, max_length=128)
    causation_id: Optional[str] = Field(None, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: int) -> int:
        if value < 0:
            raise ValueError("timestamp must be a non-negative Unix timestamp")
        return value

    def stable_sort_key(self) -> tuple[int, int, str]:
        return (self.timestamp, self.sequence, self.event_id)


# ── Deterministic snapshot ───────────────────────────────────────────────────

class MemorySnapshot(BaseModel):
    """Deterministic project-scoped projection of engineering memory."""

    version: int = Field(..., ge=0)
    generated_at: int = Field(default_factory=_utc_now)
    generated_by: Optional[str] = Field(None, max_length=256)
    project_id: str = Field(..., min_length=1, max_length=128)
    event_count: int = Field(default=0, ge=0)
    memories: List[MemoryRecord] = Field(default_factory=list)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        return _validate_schema(value)

    def integrity_hash(self) -> str:
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

def new_memory_id() -> str:
    return "mem_" + secrets.token_hex(6)


def new_memory_event_id() -> str:
    return "mevt_" + secrets.token_hex(8)
