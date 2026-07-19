"""Append-only persistence for Hermes Specialized Agent Roles.

Each project owns one isolated JSONL journal. Every record contains a
monotonic sequence number and SHA-256 checksum. Replay verifies the complete
journal before exposing reconstructed state.

This module persists governed domain records only. It does not assign work,
launch agents, schedule execution, or perform lifecycle transitions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    AgentRole,
    Assignment,
    AssignmentHandoff,
    AssignmentResult,
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
)


PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
JOURNAL_FILENAME = "agent_roles.jsonl"


class AgentRoleStoreError(RuntimeError):
    """Base error raised by the specialized-agent-role store."""


class InvalidProjectIdError(AgentRoleStoreError):
    """Raised when a project identifier cannot safely become a path."""


class JournalCorruptionError(AgentRoleStoreError):
    """Raised when a journal cannot be verified or deterministically replayed."""


class DuplicateRecordError(AgentRoleStoreError):
    """Raised when an immutable record identifier is reused."""


class AssignmentVersionError(AgentRoleStoreError):
    """Raised when assignment snapshots are appended out of order."""


class JournalEventType:
    """Stable serialized event names used in the project journal."""

    ROLE_REGISTERED = "role_registered"
    ASSIGNMENT_RECORDED = "assignment_recorded"
    HANDOFF_RECORDED = "handoff_recorded"
    RESULT_RECORDED = "result_recorded"

    ALL = frozenset(
        {
            ROLE_REGISTERED,
            ASSIGNMENT_RECORDED,
            HANDOFF_RECORDED,
            RESULT_RECORDED,
        }
    )


class JournalRecord(BaseModel):
    """One checksummed append-only project journal record."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    event_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=256)
    sequence: int = Field(..., ge=1)
    event_type: str = Field(..., min_length=1, max_length=128)
    timestamp: int = Field(..., ge=0)
    payload: Dict[str, Any]
    checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"schema version {value} not supported "
                f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )

        return value

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: str) -> str:
        if value not in JournalEventType.ALL:
            raise ValueError(f"unknown journal event type: {value}")

        return value

    @field_validator("checksum")
    @classmethod
    def _validate_checksum_shape(cls, value: str) -> str:
        normalised = value.lower()

        if any(character not in "0123456789abcdef" for character in normalised):
            raise ValueError("checksum must contain lowercase hexadecimal")

        return normalised


class AgentRoleProjectState(BaseModel):
    """Deterministically reconstructed state for one project."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    sequence: int = Field(default=0, ge=0)
    roles: Dict[str, AgentRole] = Field(default_factory=dict)
    assignments: Dict[str, Assignment] = Field(default_factory=dict)
    handoffs: Tuple[AssignmentHandoff, ...] = Field(default_factory=tuple)
    results: Tuple[AssignmentResult, ...] = Field(default_factory=tuple)

    def get_role(self, role_id: str) -> Optional[AgentRole]:
        """Return one role definition when registered."""
        return self.roles.get(role_id)

    def get_assignment(self, assignment_id: str) -> Optional[Assignment]:
        """Return the latest snapshot of one assignment."""
        return self.assignments.get(assignment_id)


def _canonical_json(value: Dict[str, Any]) -> str:
    """Serialize a mapping deterministically for hashing and storage."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _record_checksum(
    *,
    schema_version: int,
    event_id: str,
    project_id: str,
    sequence: int,
    event_type: str,
    timestamp: int,
    payload: Dict[str, Any],
) -> str:
    """Calculate a stable SHA-256 checksum for a journal record."""
    checksum_input = {
        "schema_version": schema_version,
        "event_id": event_id,
        "project_id": project_id,
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": timestamp,
        "payload": payload,
    }

    return hashlib.sha256(
        _canonical_json(checksum_input).encode("utf-8")
    ).hexdigest()


def _new_event_id() -> str:
    """Return a locally unique journal event identifier."""
    return f"roleevt_{secrets.token_hex(8)}"


def _validate_project_id(project_id: str) -> str:
    """Validate that a project ID is safe as one path component."""
    if project_id != project_id.strip():
        raise InvalidProjectIdError(
            "project_id must not contain leading or trailing whitespace"
        )

    normalised = project_id

    if not PROJECT_ID_PATTERN.fullmatch(normalised):
        raise InvalidProjectIdError(
            "project_id must contain only letters, numbers, '.', '_', or "
            "'-', must not contain path separators, and must be 1-256 "
            "characters"
        )

    if normalised in {".", ".."}:
        raise InvalidProjectIdError(
            "project_id must not be '.' or '..'"
        )

    return normalised


class AgentRoleStore:
    """Append-only, project-isolated specialized-agent-role store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._write_lock = threading.RLock()

    def project_directory(self, project_id: str) -> Path:
        """Return the isolated directory for a validated project."""
        safe_project_id = _validate_project_id(project_id)
        return self.root / safe_project_id

    def journal_path(self, project_id: str) -> Path:
        """Return the isolated append-only journal path."""
        return self.project_directory(project_id) / JOURNAL_FILENAME

    def replay(self, project_id: str) -> AgentRoleProjectState:
        """Verify and deterministically reconstruct one project's state."""
        safe_project_id = _validate_project_id(project_id)
        records = tuple(self._read_records(safe_project_id))

        roles: Dict[str, AgentRole] = {}
        assignments: Dict[str, Assignment] = {}
        handoffs: list[AssignmentHandoff] = []
        results: list[AssignmentResult] = []

        seen_event_ids: set[str] = set()
        seen_handoff_ids: set[str] = set()
        seen_result_ids: set[str] = set()
        expected_sequence = 1

        for record in records:
            if record.project_id != safe_project_id:
                raise JournalCorruptionError(
                    "journal record project_id does not match its "
                    "project directory"
                )

            if record.sequence != expected_sequence:
                raise JournalCorruptionError(
                    "journal sequence is not contiguous: "
                    f"expected {expected_sequence}, got {record.sequence}"
                )

            if record.event_id in seen_event_ids:
                raise JournalCorruptionError(
                    f"duplicate journal event_id: {record.event_id}"
                )

            seen_event_ids.add(record.event_id)
            expected_sequence += 1

            if record.event_type == JournalEventType.ROLE_REGISTERED:
                role = self._decode_role(record)

                if role.role_id in roles:
                    raise JournalCorruptionError(
                        f"duplicate role_id in journal: {role.role_id}"
                    )

                roles[role.role_id] = role
                continue

            if record.event_type == JournalEventType.ASSIGNMENT_RECORDED:
                assignment = self._decode_assignment(record)
                previous = assignments.get(assignment.assignment_id)

                if previous is None:
                    if assignment.version != 1:
                        raise JournalCorruptionError(
                            "first assignment snapshot must have version 1"
                        )
                elif assignment.version != previous.version + 1:
                    raise JournalCorruptionError(
                        "assignment versions must increase by exactly one"
                    )

                assignments[assignment.assignment_id] = assignment
                continue

            if record.event_type == JournalEventType.HANDOFF_RECORDED:
                handoff = self._decode_handoff(record)

                if handoff.handoff_id in seen_handoff_ids:
                    raise JournalCorruptionError(
                        f"duplicate handoff_id: {handoff.handoff_id}"
                    )

                seen_handoff_ids.add(handoff.handoff_id)
                handoffs.append(handoff)
                continue

            if record.event_type == JournalEventType.RESULT_RECORDED:
                result = self._decode_result(record)

                if result.result_id in seen_result_ids:
                    raise JournalCorruptionError(
                        f"duplicate result_id: {result.result_id}"
                    )

                seen_result_ids.add(result.result_id)
                results.append(result)
                continue

            raise JournalCorruptionError(
                f"unsupported event type: {record.event_type}"
            )

        return AgentRoleProjectState(
            project_id=safe_project_id,
            sequence=len(records),
            roles=roles,
            assignments=assignments,
            handoffs=tuple(handoffs),
            results=tuple(results),
        )

    def append_role(
        self,
        project_id: str,
        role: AgentRole,
        *,
        timestamp: int,
    ) -> JournalRecord:
        """Append one immutable role registration."""
        safe_project_id = _validate_project_id(project_id)

        with self._write_lock:
            state = self.replay(safe_project_id)

            if role.role_id in state.roles:
                raise DuplicateRecordError(
                    f"role_id already registered: {role.role_id}"
                )

            return self._append(
                project_id=safe_project_id,
                event_type=JournalEventType.ROLE_REGISTERED,
                timestamp=timestamp,
                payload=role.model_dump(mode="json"),
                next_sequence=state.sequence + 1,
            )

    def append_assignment(
        self,
        assignment: Assignment,
        *,
        timestamp: int,
    ) -> JournalRecord:
        """Append the next immutable snapshot of one assignment."""
        safe_project_id = _validate_project_id(assignment.project_id)

        with self._write_lock:
            state = self.replay(safe_project_id)
            previous = state.assignments.get(assignment.assignment_id)

            if previous is None:
                expected_version = 1
            else:
                expected_version = previous.version + 1

            if assignment.version != expected_version:
                raise AssignmentVersionError(
                    f"assignment {assignment.assignment_id} expected "
                    f"version {expected_version}, got {assignment.version}"
                )

            return self._append(
                project_id=safe_project_id,
                event_type=JournalEventType.ASSIGNMENT_RECORDED,
                timestamp=timestamp,
                payload=assignment.model_dump(mode="json"),
                next_sequence=state.sequence + 1,
            )

    def append_handoff(
        self,
        handoff: AssignmentHandoff,
    ) -> JournalRecord:
        """Append one immutable assignment handoff."""
        safe_project_id = _validate_project_id(handoff.project_id)

        with self._write_lock:
            state = self.replay(safe_project_id)

            if any(
                existing.handoff_id == handoff.handoff_id
                for existing in state.handoffs
            ):
                raise DuplicateRecordError(
                    f"handoff_id already exists: {handoff.handoff_id}"
                )

            return self._append(
                project_id=safe_project_id,
                event_type=JournalEventType.HANDOFF_RECORDED,
                timestamp=handoff.timestamp,
                payload=handoff.model_dump(mode="json"),
                next_sequence=state.sequence + 1,
            )

    def append_result(
        self,
        result: AssignmentResult,
    ) -> JournalRecord:
        """Append one immutable assignment result."""
        safe_project_id = _validate_project_id(result.project_id)

        with self._write_lock:
            state = self.replay(safe_project_id)

            if any(
                existing.result_id == result.result_id
                for existing in state.results
            ):
                raise DuplicateRecordError(
                    f"result_id already exists: {result.result_id}"
                )

            return self._append(
                project_id=safe_project_id,
                event_type=JournalEventType.RESULT_RECORDED,
                timestamp=result.completed_at,
                payload=result.model_dump(mode="json"),
                next_sequence=state.sequence + 1,
            )

    def _append(
        self,
        *,
        project_id: str,
        event_type: str,
        timestamp: int,
        payload: Dict[str, Any],
        next_sequence: int,
    ) -> JournalRecord:
        if timestamp < 0:
            raise ValueError(
                "journal timestamps must be non-negative Unix timestamps"
            )

        event_id = _new_event_id()
        checksum = _record_checksum(
            schema_version=CURRENT_SCHEMA_VERSION,
            event_id=event_id,
            project_id=project_id,
            sequence=next_sequence,
            event_type=event_type,
            timestamp=timestamp,
            payload=payload,
        )

        record = JournalRecord(
            schema_version=CURRENT_SCHEMA_VERSION,
            event_id=event_id,
            project_id=project_id,
            sequence=next_sequence,
            event_type=event_type,
            timestamp=timestamp,
            payload=payload,
            checksum=checksum,
        )

        journal_path = self.journal_path(project_id)
        journal_path.parent.mkdir(parents=True, exist_ok=True)

        serialized = (
            _canonical_json(record.model_dump(mode="json")) + "\n"
        ).encode("utf-8")

        descriptor = os.open(
            journal_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )

        try:
            os.write(descriptor, serialized)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        return record

    def _read_records(
        self,
        project_id: str,
    ) -> Iterable[JournalRecord]:
        journal_path = self.journal_path(project_id)

        if not journal_path.exists():
            return ()

        records: list[JournalRecord] = []

        try:
            with journal_path.open("r", encoding="utf-8") as journal:
                for line_number, raw_line in enumerate(journal, start=1):
                    if not raw_line.endswith("\n"):
                        raise JournalCorruptionError(
                            "journal contains an incomplete final record "
                            f"at line {line_number}"
                        )

                    stripped = raw_line.strip()

                    if not stripped:
                        raise JournalCorruptionError(
                            f"journal contains a blank line at {line_number}"
                        )

                    try:
                        raw_record = json.loads(stripped)
                    except json.JSONDecodeError as error:
                        raise JournalCorruptionError(
                            f"invalid JSON at journal line {line_number}"
                        ) from error

                    try:
                        record = JournalRecord.model_validate(raw_record)
                    except Exception as error:
                        raise JournalCorruptionError(
                            f"invalid journal record at line {line_number}"
                        ) from error

                    expected_checksum = _record_checksum(
                        schema_version=record.schema_version,
                        event_id=record.event_id,
                        project_id=record.project_id,
                        sequence=record.sequence,
                        event_type=record.event_type,
                        timestamp=record.timestamp,
                        payload=record.payload,
                    )

                    if record.checksum != expected_checksum:
                        raise JournalCorruptionError(
                            f"checksum mismatch at journal line {line_number}"
                        )

                    records.append(record)
        except OSError as error:
            raise AgentRoleStoreError(
                f"unable to read journal: {journal_path}"
            ) from error

        return tuple(records)

    @staticmethod
    def _decode_role(record: JournalRecord) -> AgentRole:
        try:
            return AgentRole.model_validate(record.payload)
        except Exception as error:
            raise JournalCorruptionError(
                "role journal payload is invalid"
            ) from error

    @staticmethod
    def _decode_assignment(record: JournalRecord) -> Assignment:
        try:
            assignment = Assignment.model_validate(record.payload)
        except Exception as error:
            raise JournalCorruptionError(
                "assignment journal payload is invalid"
            ) from error

        if assignment.project_id != record.project_id:
            raise JournalCorruptionError(
                "assignment payload project_id does not match record"
            )

        return assignment

    @staticmethod
    def _decode_handoff(record: JournalRecord) -> AssignmentHandoff:
        try:
            handoff = AssignmentHandoff.model_validate(record.payload)
        except Exception as error:
            raise JournalCorruptionError(
                "handoff journal payload is invalid"
            ) from error

        if handoff.project_id != record.project_id:
            raise JournalCorruptionError(
                "handoff payload project_id does not match record"
            )

        return handoff

    @staticmethod
    def _decode_result(record: JournalRecord) -> AssignmentResult:
        try:
            result = AssignmentResult.model_validate(record.payload)
        except Exception as error:
            raise JournalCorruptionError(
                "result journal payload is invalid"
            ) from error

        if result.project_id != record.project_id:
            raise JournalCorruptionError(
                "result payload project_id does not match record"
            )

        return result
