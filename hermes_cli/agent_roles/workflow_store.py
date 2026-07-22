"""Append-only, project-isolated persistence for governed workflows."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the in-process lock.
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field

from .workflow import GovernedWorkflow


class WorkflowJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(..., ge=1)
    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    workflow_version: int = Field(..., ge=1)
    previous_fingerprint: Optional[str] = Field(default=None, min_length=64, max_length=64)
    workflow: GovernedWorkflow


class GovernedWorkflowStore:
    """Persist full immutable revisions; replay rejects forks and corruption."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._write_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        project_id = project_id.strip()
        if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
            raise ValueError("invalid workflow project_id")
        return self.root / project_id / "governed-workflows.jsonl"

    @contextmanager
    def write_lock(self, project_id: str) -> Iterator[None]:
        """Serialize revision checks and appends across store instances."""
        path = self.journal_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        with self._write_lock:
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                os.chmod(lock_path, 0o600)
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def append(self, workflow: GovernedWorkflow, *, expected_version: Optional[int] = None) -> None:
        with self.write_lock(workflow.project_id):
            current = self.get(workflow.project_id, workflow.workflow_id)
            if current is None:
                if workflow.version != 1 or expected_version not in {None, 0}:
                    raise ValueError("new workflow must begin at version 1")
                previous = None
            else:
                required = current.version if expected_version is None else expected_version
                if required != current.version:
                    raise ValueError("workflow expected_version conflict")
                if workflow.version != current.version + 1:
                    raise ValueError("workflow revisions must advance by exactly one")
                previous = current.fingerprint

            path = self.journal_path(workflow.project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            records = self._read(workflow.project_id)
            record = WorkflowJournalRecord(
                sequence=len(records) + 1,
                project_id=workflow.project_id,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                previous_fingerprint=previous,
                workflow=workflow,
            )
            line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)

    def get(self, project_id: str, workflow_id: str) -> Optional[GovernedWorkflow]:
        found = [record.workflow for record in self._read(project_id) if record.workflow_id == workflow_id]
        return found[-1] if found else None

    def list(self, project_id: str) -> Tuple[GovernedWorkflow, ...]:
        latest: dict[str, GovernedWorkflow] = {}
        for record in self._read(project_id):
            latest[record.workflow_id] = record.workflow
        return tuple(latest[key] for key in sorted(latest))

    def _read(self, project_id: str) -> Tuple[WorkflowJournalRecord, ...]:
        path = self.journal_path(project_id)
        if not path.exists():
            return ()
        records = []
        latest: dict[str, GovernedWorkflow] = {}
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = WorkflowJournalRecord.model_validate_json(raw)
            except Exception as exc:
                raise ValueError(f"corrupt workflow journal line {line_number}") from exc
            if record.sequence != line_number or record.project_id != project_id:
                raise ValueError("workflow journal sequence or project mismatch")
            prior = latest.get(record.workflow_id)
            if prior is None:
                if record.workflow_version != 1 or record.previous_fingerprint is not None:
                    raise ValueError("invalid initial workflow revision")
            elif (
                record.workflow_version != prior.version + 1
                or record.previous_fingerprint != prior.fingerprint
            ):
                raise ValueError("workflow revision chain is invalid")
            latest[record.workflow_id] = record.workflow
            records.append(record)
        return tuple(records)
