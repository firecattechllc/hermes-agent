"""Append-only persistence and queries for workflow execution evidence."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Protocol, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the in-process lock.
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflow_execution import (
    WorkflowExecutionEvent,
    WorkflowExecutionProjector,
    WorkflowRunStatus,
    WorkflowRunSummary,
)


WORKFLOW_EXECUTION_JOURNAL = "workflow-execution-evidence.jsonl"


def _safe_project_id(project_id: str) -> str:
    project_id = project_id.strip()
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
    ):
        raise ValueError("invalid workflow execution project_id")
    return project_id


class WorkflowExecutionJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    project_id: str = Field(..., min_length=1, max_length=128)
    run_id: str = Field(..., min_length=1, max_length=128)
    event: WorkflowExecutionEvent
    checksum: str = Field(..., min_length=64, max_length=64)

    @model_validator(mode="after")
    def _validate_associations(self) -> "WorkflowExecutionJournalRecord":
        if self.project_id != self.event.project_id or self.run_id != self.event.run_id:
            raise ValueError("workflow execution journal association mismatch")
        expected = self.calculate_checksum(
            self.journal_sequence,
            self.project_id,
            self.run_id,
            self.event,
        )
        if self.checksum != expected:
            raise ValueError("workflow execution journal checksum mismatch")
        return self

    @staticmethod
    def calculate_checksum(
        journal_sequence: int,
        project_id: str,
        run_id: str,
        event: WorkflowExecutionEvent,
    ) -> str:
        payload = {
            "journal_sequence": journal_sequence,
            "project_id": project_id,
            "run_id": run_id,
            "event": event.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class WorkflowExecutionStore:
    """Project-isolated evidence ledger with deterministic replay queries."""

    def __init__(
        self,
        root: Path,
        projector: Optional[WorkflowExecutionProjector] = None,
    ) -> None:
        self.root = Path(root)
        self.projector = projector or WorkflowExecutionProjector()
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        project_id = _safe_project_id(project_id)
        return self.root / project_id / WORKFLOW_EXECUTION_JOURNAL

    @contextmanager
    def write_lock(self, project_id: str) -> Iterator[None]:
        path = self.journal_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        with self._thread_lock:
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                os.chmod(lock_path, 0o600)
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def append(self, event: WorkflowExecutionEvent) -> WorkflowExecutionEvent:
        """Validate and append once; identical event IDs are idempotent."""
        with self.write_lock(event.project_id):
            records = self._read_unlocked(event.project_id)
            for record in records:
                if record.event.event_id != event.event_id:
                    continue
                if record.event != event:
                    raise ValueError("workflow execution event ID collision")
                return record.event

            run_events = tuple(
                record.event for record in records if record.run_id == event.run_id
            )
            self.projector.replay(run_events + (event,))
            record = WorkflowExecutionJournalRecord(
                journal_sequence=len(records) + 1,
                project_id=event.project_id,
                run_id=event.run_id,
                event=event,
                checksum=WorkflowExecutionJournalRecord.calculate_checksum(
                    len(records) + 1,
                    event.project_id,
                    event.run_id,
                    event,
                ),
            )
            line = json.dumps(
                record.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            path = self.journal_path(event.project_id)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                remaining = memoryview((line + "\n").encode())
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("workflow execution journal write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return event

    def events_for_run(
        self,
        project_id: str,
        run_id: str,
    ) -> Tuple[WorkflowExecutionEvent, ...]:
        events = tuple(
            record.event
            for record in self._read(project_id)
            if record.run_id == run_id.strip()
        )
        return tuple(sorted(events, key=lambda item: item.sequence))

    def get_summary(
        self,
        project_id: str,
        run_id: str,
    ) -> Optional[WorkflowRunSummary]:
        events = self.events_for_run(project_id, run_id)
        return self.projector.replay(events) if events else None

    def list_summaries(
        self,
        project_id: str,
        *,
        workflow_id: Optional[str] = None,
        status: Optional[WorkflowRunStatus] = None,
        node_run_id: Optional[str] = None,
    ) -> Tuple[WorkflowRunSummary, ...]:
        run_ids = sorted({record.run_id for record in self._read(project_id)})
        summaries = tuple(
            self.projector.replay(self.events_for_run(project_id, run_id))
            for run_id in run_ids
        )
        if workflow_id is not None:
            workflow_id = workflow_id.strip()
            summaries = tuple(
                item for item in summaries if item.workflow_id == workflow_id
            )
        if status is not None:
            summaries = tuple(item for item in summaries if item.status == status)
        if node_run_id is not None:
            node_run_id = node_run_id.strip()
            summaries = tuple(
                item
                for item in summaries
                if any(node.node_run_id == node_run_id for node in item.nodes)
            )
        return tuple(sorted(summaries, key=lambda item: item.run_id))

    def _read(self, project_id: str) -> Tuple[WorkflowExecutionJournalRecord, ...]:
        project_id = _safe_project_id(project_id)
        path = self.journal_path(project_id)
        if not path.exists():
            return ()
        with self.write_lock(project_id):
            return self._read_unlocked(project_id)

    def _read_unlocked(
        self,
        project_id: str,
    ) -> Tuple[WorkflowExecutionJournalRecord, ...]:
        project_id = _safe_project_id(project_id)
        path = self.journal_path(project_id)
        if not path.exists():
            return ()
        records = []
        for line_number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            try:
                record = WorkflowExecutionJournalRecord.model_validate_json(raw)
            except Exception as exc:
                raise ValueError(
                    f"corrupt workflow execution journal line {line_number}"
                ) from exc
            if record.journal_sequence != line_number:
                raise ValueError("workflow execution journal sequence is not contiguous")
            if record.project_id != project_id:
                raise ValueError("workflow execution journal project mismatch")
            records.append(record)
        return tuple(records)


class _VisibilityPublisher(Protocol):
    def publish(self, summary: WorkflowRunSummary): ...


class WorkflowExecutionVisibilityError(RuntimeError):
    """Raised after evidence persistence when visibility publication fails."""

    def __init__(self, summary: WorkflowRunSummary) -> None:
        super().__init__(
            "workflow execution evidence persisted but visibility publication "
            "failed; reconcile before relying on Mission Control"
        )
        self.summary = summary


class WorkflowExecutionRecorder:
    """Persistence-first application boundary for execution evidence."""

    def __init__(
        self,
        store: WorkflowExecutionStore,
        visibility: Optional[_VisibilityPublisher] = None,
    ) -> None:
        self.store = store
        self.visibility = visibility

    def record(self, event: WorkflowExecutionEvent) -> WorkflowRunSummary:
        self.store.append(event)
        summary = self.store.get_summary(event.project_id, event.run_id)
        if summary is None:  # pragma: no cover - append guarantees a run exists.
            raise RuntimeError("persisted workflow execution run is missing")
        if self.visibility is not None:
            try:
                self.visibility.publish(summary)
            except Exception as exc:
                raise WorkflowExecutionVisibilityError(summary) from exc
        return summary

    def reconcile_visibility(
        self,
        project_id: str,
    ) -> Tuple[WorkflowRunSummary, ...]:
        summaries = self.store.list_summaries(project_id)
        if self.visibility is not None:
            for summary in summaries:
                self.visibility.publish(summary)
        return summaries
