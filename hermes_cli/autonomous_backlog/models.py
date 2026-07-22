"""Domain models for Hermes Governed Autonomous Backlog Execution.

The autonomous backlog converts approved engineering intentions into durable,
project-scoped work items. It does not execute work itself. It records what may
be worked on, which governance constraints apply, and the item's lifecycle.

This subsystem is distinct from:

- Shared Engineering Context, which represents current project truth.
- Mission Control, which represents operational telemetry and projected state.
- Structured Engineering Memory, which preserves governed durable knowledge.
- Cron, which determines when scheduled ticks occur.

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
    """Return the current UTC Unix timestamp as an integer."""
    return int(time.time())


def _validate_schema(version: int) -> int:
    """Reject unknown model schema versions."""
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    return version


def _new_identifier(prefix: str) -> str:
    """Create a locally unique, opaque domain identifier."""
    normalised_prefix = prefix.strip()

    if not normalised_prefix:
        raise ValueError("identifier prefix must not be empty")

    return f"{normalised_prefix}_{secrets.token_hex(8)}"


def new_backlog_item_id() -> str:
    """Create an autonomous backlog item identifier."""
    return _new_identifier("backlog")


def new_backlog_event_id() -> str:
    """Create an autonomous backlog event identifier."""
    return _new_identifier("bevt")


# ── Enums ────────────────────────────────────────────────────────────────────

class BacklogStatus(str, Enum):
    """Governed lifecycle states for one backlog item."""

    CANDIDATE = "candidate"
    TRIAGED = "triaged"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    CLAIMED = "claimed"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class BacklogPriority(str, Enum):
    """Relative scheduling priority.

    Priority influences selection order but never overrides governance,
    dependency, risk, or concurrency constraints.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class BacklogRiskLevel(str, Enum):
    """Estimated execution risk for a backlog item."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BacklogSourceType(str, Enum):
    """Origin from which a backlog candidate was derived."""

    HUMAN = "human"
    CONTEXT_RECORD = "context_record"
    CONTEXT_OBJECTIVE = "context_objective"
    CONTEXT_BLOCKER = "context_blocker"
    MISSION_CONTROL_EVENT = "mission_control_event"
    ENGINEERING_MEMORY = "engineering_memory"
    TEST_RESULT = "test_result"
    TOOL_EXECUTION = "tool_execution"
    AGENT_OBSERVATION = "agent_observation"
    IMPORT = "import"


class BacklogEventType(str, Enum):
    """Append-only lifecycle event types."""

    CREATED = "backlog_item_created"
    TRIAGED = "backlog_item_triaged"
    APPROVED = "backlog_item_approved"
    SCHEDULED = "backlog_item_scheduled"
    CLAIMED = "backlog_item_claimed"
    PLANNING_STARTED = "backlog_planning_started"
    EXECUTION_STARTED = "backlog_execution_started"
    VERIFICATION_STARTED = "backlog_verification_started"
    APPROVAL_REQUESTED = "backlog_approval_requested"
    BLOCKED = "backlog_item_blocked"
    FAILED = "backlog_item_failed"
    COMPLETED = "backlog_item_completed"
    CANCELLED = "backlog_item_cancelled"
    SUPERSEDED = "backlog_item_superseded"
    MARKED_UNKNOWN = "backlog_item_marked_unknown"


class ScheduleMode(str, Enum):
    """How an approved backlog item becomes eligible for selection."""

    MANUAL = "manual"
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"


class RetryMode(str, Enum):
    """Permitted automatic retry behavior."""

    NEVER = "never"
    MANUAL = "manual"
    BOUNDED = "bounded"


# ── Immutable policy and provenance models ──────────────────────────────────

class BacklogSource(BaseModel):
    """Immutable origin information for a backlog candidate."""

    model_config = ConfigDict(frozen=True)

    source_type: BacklogSourceType
    source_refs: Tuple[str, ...] = Field(default_factory=tuple)
    captured_at: int = Field(default_factory=_utc_now)
    captured_by: Optional[str] = Field(None, max_length=256)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_refs")
    @classmethod
    def _normalise_source_refs(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
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
    def _check_captured_at(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                "captured_at must be a non-negative Unix timestamp"
            )

        return value


class EvidenceRequirement(BaseModel):
    """Evidence that must exist before an item may complete."""

    requirement_id: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=2048)
    required: bool = True
    evidence_type: Optional[str] = Field(None, max_length=128)

    @field_validator("requirement_id", "description", "evidence_type")
    @classmethod
    def _strip_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("text fields must not be blank")

        return normalised


class SchedulePolicy(BaseModel):
    """Controls when an approved item becomes selection-eligible."""

    mode: ScheduleMode = ScheduleMode.MANUAL
    scheduled_at: Optional[int] = None
    not_before: Optional[int] = None
    expires_at: Optional[int] = None

    @field_validator("scheduled_at", "not_before", "expires_at")
    @classmethod
    def _check_timestamps(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError(
                "schedule timestamps must be non-negative Unix timestamps"
            )

        return value

    @model_validator(mode="after")
    def _validate_schedule(self) -> "SchedulePolicy":
        if self.mode == ScheduleMode.SCHEDULED and self.scheduled_at is None:
            raise ValueError(
                "scheduled mode requires scheduled_at"
            )

        if self.mode != ScheduleMode.SCHEDULED and self.scheduled_at is not None:
            raise ValueError(
                "scheduled_at is only valid for scheduled mode"
            )

        if (
            self.not_before is not None
            and self.expires_at is not None
            and self.expires_at < self.not_before
        ):
            raise ValueError(
                "expires_at must not be earlier than not_before"
            )

        return self


class RetryPolicy(BaseModel):
    """Controls whether and how failed work may be retried."""

    mode: RetryMode = RetryMode.NEVER
    max_attempts: int = Field(default=1, ge=1, le=100)
    backoff_seconds: int = Field(default=0, ge=0, le=604800)

    @model_validator(mode="after")
    def _validate_retry_policy(self) -> "RetryPolicy":
        if self.mode in {RetryMode.NEVER, RetryMode.MANUAL}:
            if self.max_attempts != 1:
                raise ValueError(
                    f"{self.mode.value} retry mode requires max_attempts=1"
                )

            if self.backoff_seconds != 0:
                raise ValueError(
                    f"{self.mode.value} retry mode requires "
                    "backoff_seconds=0"
                )

        if self.mode == RetryMode.BOUNDED and self.max_attempts < 2:
            raise ValueError(
                "bounded retry mode requires max_attempts of at least 2"
            )

        return self


# ── Backlog item ─────────────────────────────────────────────────────────────

class BacklogItem(BaseModel):
    """A durable, governed unit of autonomous engineering work."""

    item_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)

    title: str = Field(..., min_length=1, max_length=512)
    description: str = Field(..., min_length=1, max_length=16384)

    status: BacklogStatus = BacklogStatus.CANDIDATE
    priority: BacklogPriority = BacklogPriority.NORMAL
    risk_level: BacklogRiskLevel = BacklogRiskLevel.MEDIUM

    source: BacklogSource

    dependencies: List[str] = Field(default_factory=list)
    blocked_by: List[str] = Field(default_factory=list)

    acceptance_criteria: List[str] = Field(default_factory=list)
    evidence_requirements: List[EvidenceRequirement] = Field(
        default_factory=list
    )
    evidence_refs: List[str] = Field(default_factory=list)

    required_capabilities: List[str] = Field(default_factory=list)
    allowed_paths: List[str] = Field(default_factory=list)
    denied_paths: List[str] = Field(default_factory=list)

    execution_policy_id: Optional[str] = Field(None, max_length=128)
    schedule_policy: SchedulePolicy = Field(default_factory=SchedulePolicy)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)

    created_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    created_by: Optional[str] = Field(None, max_length=256)
    correlation_id: Optional[str] = Field(None, max_length=128)

    version: int = Field(default=1, ge=1)
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    superseded_by: Optional[str] = Field(None, max_length=128)
    failure_reason: Optional[str] = Field(None, max_length=4096)
    blocked_reason: Optional[str] = Field(None, max_length=4096)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _check_timestamps(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                "timestamps must be non-negative Unix timestamps"
            )

        return value

    @field_validator(
        "item_id",
        "project_id",
        "title",
        "description",
        "execution_policy_id",
        "created_by",
        "correlation_id",
        "superseded_by",
        "failure_reason",
        "blocked_reason",
    )
    @classmethod
    def _strip_scalar_text(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("text fields must not be blank")

        return normalised

    @field_validator(
        "dependencies",
        "blocked_by",
        "acceptance_criteria",
        "evidence_refs",
        "required_capabilities",
        "allowed_paths",
        "denied_paths",
    )
    @classmethod
    def _normalise_string_lists(
        cls,
        values: List[str],
    ) -> List[str]:
        seen: set[str] = set()
        output: list[str] = []

        for value in values:
            normalised = value.strip()

            if normalised and normalised not in seen:
                seen.add(normalised)
                output.append(normalised)

        return output

    @model_validator(mode="after")
    def _validate_item(self) -> "BacklogItem":
        if self.updated_at < self.created_at:
            self.updated_at = self.created_at

        if self.item_id in self.dependencies:
            raise ValueError("backlog item cannot depend on itself")

        if self.item_id in self.blocked_by:
            raise ValueError("backlog item cannot be blocked by itself")

        overlap = set(self.allowed_paths) & set(self.denied_paths)
        if overlap:
            raise ValueError(
                "paths cannot be both allowed and denied: "
                f"{sorted(overlap)}"
            )

        if (
            self.status == BacklogStatus.SUPERSEDED
            and not self.superseded_by
        ):
            raise ValueError(
                "superseded backlog item requires superseded_by"
            )

        if self.superseded_by == self.item_id:
            raise ValueError(
                "backlog item cannot supersede itself"
            )

        if self.status == BacklogStatus.FAILED and not self.failure_reason:
            raise ValueError(
                "failed backlog item requires failure_reason"
            )

        if self.status == BacklogStatus.BLOCKED and not self.blocked_reason:
            raise ValueError(
                "blocked backlog item requires blocked_reason"
            )

        if self.status == BacklogStatus.COMPLETED:
            required_ids = {
                requirement.requirement_id
                for requirement in self.evidence_requirements
                if requirement.required
            }

            supplied_refs = set(self.evidence_refs)

            if required_ids and not supplied_refs:
                raise ValueError(
                    "completed backlog item requires evidence_refs"
                )

        if (
            self.status == BacklogStatus.UNKNOWN
            and self.retry_policy.mode == RetryMode.BOUNDED
        ):
            raise ValueError(
                "unknown backlog item cannot permit automatic bounded retry"
            )

        return self

    def content_fingerprint(self) -> str:
        """Return a deterministic semantic fingerprint.

        Identity, timestamps, lifecycle state, evidence, and mutable execution
        details are intentionally excluded.
        """
        canonical = json.dumps(
            {
                "project_id": self.project_id,
                "title": self.title.strip(),
                "description": self.description.strip(),
                "priority": self.priority.value,
                "risk_level": self.risk_level.value,
                "source_type": self.source.source_type.value,
                "source_refs": list(self.source.source_refs),
                "dependencies": sorted(self.dependencies),
                "acceptance_criteria": self.acceptance_criteria,
                "required_capabilities": sorted(
                    self.required_capabilities
                ),
                "allowed_paths": sorted(self.allowed_paths),
                "denied_paths": sorted(self.denied_paths),
                "execution_policy_id": self.execution_policy_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()[:32]


# ── Append-only lifecycle event ──────────────────────────────────────────────

class BacklogEvent(BaseModel):
    """Immutable lifecycle event for deterministic backlog replay."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: BacklogEventType

    project_id: str = Field(..., min_length=1, max_length=128)
    item_id: str = Field(..., min_length=1, max_length=128)

    timestamp: int = Field(default_factory=_utc_now)
    sequence: int = Field(..., ge=1)

    actor: Optional[str] = Field(None, max_length=256)
    correlation_id: Optional[str] = Field(None, max_length=128)
    causation_id: Optional[str] = Field(None, max_length=128)
    idempotency_key: Optional[str] = Field(None, max_length=256)

    expected_version: Optional[int] = Field(None, ge=0)
    resulting_version: int = Field(..., ge=1)

    payload: Dict[str, Any] = Field(default_factory=dict)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                "timestamp must be a non-negative Unix timestamp"
            )

        return value

    @field_validator(
        "event_id",
        "project_id",
        "item_id",
        "actor",
        "correlation_id",
        "causation_id",
        "idempotency_key",
    )
    @classmethod
    def _strip_scalar_text(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("text fields must not be blank")

        return normalised

    @model_validator(mode="after")
    def _validate_versions(self) -> "BacklogEvent":
        if (
            self.expected_version is not None
            and self.resulting_version != self.expected_version + 1
        ):
            raise ValueError(
                "resulting_version must equal expected_version + 1"
            )

        return self

    def stable_sort_key(self) -> Tuple[int, int, str]:
        """Return deterministic event ordering."""
        return (
            self.timestamp,
            self.sequence,
            self.event_id,
        )

    def integrity_hash(self) -> str:
        """Return a deterministic hash of immutable event content."""
        canonical = json.dumps(
            self.model_dump(
                mode="json",
                exclude={"schema_version"},
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()


# ── Deterministic snapshot ───────────────────────────────────────────────────

class BacklogSnapshot(BaseModel):
    """Deterministic project-scoped projection of backlog state."""

    version: int = Field(..., ge=1)
    generated_at: int = Field(default_factory=_utc_now)
    generated_by: Optional[str] = Field(None, max_length=256)

    project_id: str = Field(..., min_length=1, max_length=128)
    event_count: int = Field(..., ge=0)

    items: List[BacklogItem] = Field(default_factory=list)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator("generated_at")
    @classmethod
    def _check_generated_at(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                "generated_at must be a non-negative Unix timestamp"
            )

        return value

    @field_validator("project_id", "generated_by")
    @classmethod
    def _strip_scalar_text(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("text fields must not be blank")

        return normalised

    @model_validator(mode="after")
    def _validate_project_isolation(self) -> "BacklogSnapshot":
        mismatched = [
            item.item_id
            for item in self.items
            if item.project_id != self.project_id
        ]

        if mismatched:
            raise ValueError(
                "snapshot contains items from another project: "
                f"{sorted(mismatched)}"
            )

        item_ids = [item.item_id for item in self.items]

        if len(item_ids) != len(set(item_ids)):
            raise ValueError(
                "snapshot contains duplicate backlog item ids"
            )

        self.items = sorted(
            self.items,
            key=lambda item: item.item_id,
        )

        return self

    def integrity_hash(self) -> str:
        """Return a deterministic snapshot integrity hash.

        Generation metadata is excluded so identical projected state produces
        the same hash regardless of when or by whom it was generated.
        """
        canonical = json.dumps(
            {
                "version": self.version,
                "project_id": self.project_id,
                "event_count": self.event_count,
                "items": [
                    item.model_dump(mode="json")
                    for item in self.items
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
