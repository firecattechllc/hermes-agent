"""Local JSONL persistence for Hermes Mission Control telemetry.

Design mirrors ``hermes_cli/context_engine/store.py`` with mission-control's
own root path and distinct domain models.

File layout under ``$HERMES_HOME/mission_control/``::

  meta.json          — store manifest (schema version, created_at)
  events.jsonl       — append-only telemetry event journal
  projects/
    {project_id}/
      events.jsonl   — per-project event journal copy

Malformed / partial writes are detected and rejected (fail-closed). Project
isolation is enforced at the path and journal levels. No UI or write-action
API is exposed; all data is ingested through ``append_event()`` or the service
layer's ``ingest_context_launch()``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from hermes_cli.mission_control import models as m

logger = logging.getLogger("hermes.mission_control.store")

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

_PROCESS_LOCKS: Dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()

# ── Paths ────────────────────────────────────────────────────────────────────

def _mission_control_root() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "mission_control"


def mission_control_root() -> Path:
    return _mission_control_root()


def meta_path(*, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _mission_control_root()
    return base / "meta.json"


def event_log_path(*, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _mission_control_root()
    return base / "events.jsonl"


def project_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    safe = _safe_id(project_id)
    base = root if root is not None else _mission_control_root()
    return base / "projects" / safe


def project_event_log_path(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "events.jsonl"


def _safe_id(ident: Any) -> str:
    """Reject path traversal and other unsafe identifiers."""
    if not isinstance(ident, str):
        raise ValueError(f"unsafe identifier: {ident!r}")
    if not ident or ".." in ident or "/" in ident or "\\" in ident or "\x00" in ident:
        raise ValueError(f"unsafe identifier: {ident!r}")
    return ident


# ── JSON helpers ─────────────────────────────────────────────────────────────

def _json_loads(s: str) -> dict:
    return json.loads(s)


def _json_dumps(data: dict, *, indent: int = 2, sort_keys: bool = True) -> str:
    return json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=False)


# ── Atomic write (single-shot JSON files, not journal lines) ────────────────

def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = _preserve_mode(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".write_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_json_dumps(data))
            fh.flush()
            os.fsync(fh.fileno())
        if original_mode is not None:
            os.chmod(tmp, original_mode)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _preserve_mode(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None


# ── Validation helpers ───────────────────────────────────────────────────────

def _load_json_safe(path: Path) -> Optional[dict]:
    """Load JSON, returning None when the file does not exist.

    Existing but unreadable or malformed files fail closed with a clear error.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc

    if not raw:
        return None

    try:
        return _json_loads(raw)
    except ValueError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def _load_line_records(path: Path) -> Iterator[dict]:
    """Yield parsed JSON lines; reject any malformed line (fail-closed)."""
    if not path.exists():
        return
    try:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield _json_loads(line)
            except ValueError as exc:
                raise ValueError(
                    f"malformed JSONL line {lineno} in {path}: {exc}"
                ) from exc
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def _write_journal_lines(path: Path, lines: List[str]) -> None:
    """Write lines atomically to a JSONL journal file (append-only, durable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _event_journal_line(event: m.TelemetryEvent) -> str:
    """Validate and serialize an event before any journal mutation."""
    _safe_id(event.project_id)
    try:
        data = event.model_dump(mode="json")
        return _json_dumps(data, indent=None) + "\n"
    except Exception as exc:
        raise ValueError(
            f"telemetry event {getattr(event, 'event_id', '<unknown>')!r} "
            "payload is not JSON serializable"
        ) from exc


def _process_lock_for(root: Path) -> threading.RLock:
    key = str(root.resolve(strict=False))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


# ── Store class ──────────────────────────────────────────────────────────────

class MissionControlStore:
    """Persistent store for Hermes Mission Control telemetry.

    Thread-unsafe — use a lock at the service layer if multi-threaded access
    is needed. All mutations are atomic (partial writes cannot survive).

    Args:
        root: Explicit root path. Defaults to ``$HERMES_HOME/mission_control/``.
              Intended for tests only.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or _mission_control_root()
        self._ensure_root()

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Serialize sequence allocation and journal appends for this store."""
        lock = _process_lock_for(self._root)
        with lock:
            lock_path = self._root / ".write.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a", encoding="utf-8") as lock_fh:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        meta = self._root / "meta.json"
        if not meta.exists():
            _atomic_write(meta, {
                "schema_version": m.CURRENT_SCHEMA_VERSION,
                "created_at": m._utc_now(),
                "version": 1,
            })

    # ── Event journal ──────────────────────────────────────────────────────

    def append_event(self, event: m.TelemetryEvent) -> None:
        """Append a TelemetryEvent to the global event journal and the
        per-project journal. Both writes are atomic (appended via fsync).
        """
        with self.write_lock():
            self._append_event_unlocked(event)

    def _append_event_unlocked(self, event: m.TelemetryEvent) -> None:
        """Validate and append one event. Caller must hold ``write_lock``."""
        line = _event_journal_line(event)
        self._append_prevalidated_event(event, line)

    def _append_prevalidated_event(self, event: m.TelemetryEvent, line: str) -> None:
        """Append one prevalidated event. Caller must hold ``write_lock``."""

        # Write to global journal.
        glob_path = event_log_path(root=self._root)
        glob_path.parent.mkdir(parents=True, exist_ok=True)
        with glob_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

        # Write to per-project journal.
        proj_path = project_event_log_path(event.project_id, root=self._root)
        proj_path.parent.mkdir(parents=True, exist_ok=True)
        with proj_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def append_events(self, events: List[m.TelemetryEvent]) -> None:
        """Append multiple events in a batch (single fsync per file)."""
        if not events:
            return

        with self.write_lock():
            self._append_events_unlocked(events)

    def _append_events_unlocked(self, events: List[m.TelemetryEvent]) -> None:
        """Validate and append events. Caller must hold ``write_lock``."""
        if not events:
            return

        global_lines: List[str] = []
        project_batches: Dict[str, List[str]] = {}

        for event in events:
            line = _event_journal_line(event)
            global_lines.append(line)
            project_batches.setdefault(event.project_id, []).append(line)

        self._append_prevalidated_events(global_lines, project_batches)

    def _append_prevalidated_events(
        self,
        global_lines: List[str],
        project_batches: Dict[str, List[str]],
    ) -> None:
        """Append prevalidated event lines. Caller must hold ``write_lock``."""

        # Global journal.
        glob_path = event_log_path(root=self._root)
        glob_path.parent.mkdir(parents=True, exist_ok=True)
        with glob_path.open("a", encoding="utf-8") as fh:
            for line in global_lines:
                fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

        # Per-project journals.
        for pid, lines in project_batches.items():
            proj_path = project_event_log_path(pid, root=self._root)
            proj_path.parent.mkdir(parents=True, exist_ok=True)
            with proj_path.open("a", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())

    # ── Journal replay ────────────────────────────────────────────────────

    def iter_events(
        self,
        project_id: Optional[str] = None,
    ) -> Iterator[m.TelemetryEvent]:
        """Replay the telemetry journal, optionally scoped to one project.

        When ``project_id`` is provided, reads the per-project journal for
        isolation. When omitted, reads the global journal.
        """
        if project_id is not None:
            _safe_id(project_id)
            path = project_event_log_path(project_id, root=self._root)
        else:
            path = event_log_path(root=self._root)

        for data in _load_line_records(path):
            try:
                event = m.TelemetryEvent(**data)
            except Exception as exc:
                raise ValueError(f"invalid telemetry event in {path}: {exc}") from exc
            yield event

    def event_count(self, project_id: Optional[str] = None) -> int:
        """Count events in the journal (approximate, for snapshot versioning)."""
        if project_id is not None:
            _safe_id(project_id)
            path = project_event_log_path(project_id, root=self._root)
        else:
            path = event_log_path(root=self._root)

        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def list_project_ids(self) -> List[str]:
        """List all project IDs with per-project journals."""
        projects_dir = self._root / "projects"
        ids: List[str] = []
        if not projects_dir.exists():
            return ids
        for entry in projects_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                _safe_id(entry.name)
            except ValueError:
                continue
            if (entry / "events.jsonl").exists():
                ids.append(entry.name)
        return sorted(ids)

    # ── Deterministic replay projection ────────────────────────────────────

    def build_snapshot(
        self,
        project_id: str,
        events: Optional[List[m.TelemetryEvent]] = None,
        *,
        generated_by: Optional[str] = None,
    ) -> m.MissionControlSnapshot:
        """Deterministic snapshot from journal replay.

        When ``events`` is None, reads from the per-project journal. When
        events is provided (e.g. from an in-memory replay in the adapter),
        uses those events directly.

        Events are sorted by ``stable_sort_key()`` (timestamp → sequence →
        event_id) before projection, ensuring deterministic output regardless
        of the order in which events were appended.
        """
        _safe_id(project_id)

        if events is None:
            events = list(self.iter_events(project_id=project_id))

        if not events:
            return m.MissionControlSnapshot(
                version=0,
                generated_at=m._utc_now(),
                generated_by=generated_by,
                project_id=project_id,
                event_count=0,
            )

        # Stable sort.
        sorted_events = sorted(events, key=lambda e: e.stable_sort_key())

        # Project into state snapshots (deterministic single-pass replay).
        agent_states: Dict[str, m.AgentStateSnapshot] = {}
        backlog_states: Dict[str, m.BacklogItemStateSnapshot] = {}
        approval_states: Dict[str, m.ApprovalStateSnapshot] = {}
        evidence_states: Dict[str, m.EvidenceStateSnapshot] = {}
        promotion_states: Dict[str, m.PromotionStateSnapshot] = {}

        for event in sorted_events:
            _apply_event_to_projection(
                event, project_id,
                agent_states, backlog_states, approval_states,
                evidence_states, promotion_states,
            )

        # Version = event count (deterministic provenance).
        version = len(sorted_events)

        return m.MissionControlSnapshot(
            version=version,
            generated_at=m._utc_now(),
            generated_by=generated_by,
            project_id=project_id,
            event_count=version,
            events=sorted_events,
            agent_states=sorted(agent_states.values(), key=lambda s: s.agent_id),
            backlog_states=sorted(backlog_states.values(), key=lambda b: b.backlog_id),
            approval_states=sorted(approval_states.values(), key=lambda a: a.approval_id),
            evidence_states=sorted(evidence_states.values(), key=lambda e: e.evidence_id),
            promotion_states=sorted(promotion_states.values(), key=lambda p: p.promotion_id),
        )


# ── State projection function ─────────────────────────────────────────────────

def _apply_event_to_projection(
    event: m.TelemetryEvent,
    project_id: str,
    agent_states: Dict[str, m.AgentStateSnapshot],
    backlog_states: Dict[str, m.BacklogItemStateSnapshot],
    approval_states: Dict[str, m.ApprovalStateSnapshot],
    evidence_states: Dict[str, m.EvidenceStateSnapshot],
    promotion_states: Dict[str, m.PromotionStateSnapshot],
) -> None:
    """Apply a single TelemetryEvent to the in-progress projection state.

    This is a pure function that mutates the provided dictionaries as a
    side effect. Each event type maps to a deterministic state transition.
    """
    etype = event.event_type

    # ── Agent state transitions ──────────────────────────────────────────
    if etype == "agent_started":
        agent_id = event.agent_id or event.payload.get("agent_id", "unknown")
        agent_states[agent_id] = m.AgentStateSnapshot(
            agent_id=agent_id,
            project_id=project_id,
            launch_id=event.launch_id,
            task_id=event.task_id,
            state=m.AgentState.IDLE,
            last_event_id=event.event_id,
            last_event_type=etype,
            last_event_timestamp=event.timestamp,
            updated_at=event.timestamp,
        )
    elif etype == "agent_thinking":
        agent_id = event.agent_id or event.payload.get("agent_id", "unknown")
        if agent_id in agent_states:
            existing = agent_states[agent_id]
            data = existing.model_dump()
            data.update(
                state=m.AgentState.THINKING,
                last_event_id=event.event_id,
                last_event_type=etype,
                last_event_timestamp=event.timestamp,
                updated_at=event.timestamp,
            )
            agent_states[agent_id] = m.AgentStateSnapshot(**data)
        else:
            agent_states[agent_id] = m.AgentStateSnapshot(
                agent_id=agent_id,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                state=m.AgentState.THINKING,
                last_event_id=event.event_id,
                last_event_type=etype,
                last_event_timestamp=event.timestamp,
                updated_at=event.timestamp,
            )
    elif etype in ("agent_tools_started", "agent_tools_completed",
                   "agent_waiting_approval", "agent_blocked",
                   "agent_error", "agent_complete"):
        agent_id = event.agent_id or event.payload.get("agent_id", "unknown")
        state_map = {
            "agent_tools_started": m.AgentState.RUNNING_TOOLS,
            "agent_tools_completed": m.AgentState.IDLE,
            "agent_waiting_approval": m.AgentState.WAITING_APPROVAL,
            "agent_blocked": m.AgentState.BLOCKED,
            "agent_error": m.AgentState.ERROR,
            "agent_complete": m.AgentState.COMPLETE,
        }
        new_state = state_map[etype]
        if agent_id in agent_states:
            existing = agent_states[agent_id]
            data = existing.model_dump()
            data.update(
                state=new_state,
                last_event_id=event.event_id,
                last_event_type=etype,
                last_event_timestamp=event.timestamp,
                updated_at=event.timestamp,
            )
            agent_states[agent_id] = m.AgentStateSnapshot(**data)
        else:
            agent_states[agent_id] = m.AgentStateSnapshot(
                agent_id=agent_id,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                state=new_state,
                last_event_id=event.event_id,
                last_event_type=etype,
                last_event_timestamp=event.timestamp,
                updated_at=event.timestamp,
            )

    # ── Backlog state transitions ─────────────────────────────────────────
    elif etype == "backlog_item_created":
        bid = event.backlog_id or event.payload.get("backlog_id", "unknown")
        backlog_states[bid] = m.BacklogItemStateSnapshot(
            backlog_id=bid,
            project_id=project_id,
            launch_id=event.launch_id,
            task_id=event.task_id,
            state=m.BacklogItemState.BACKLOG,
            title=event.payload.get("title"),
            description=event.payload.get("description"),
            created_at=event.timestamp,
            updated_at=event.timestamp,
        )
    elif etype in ("backlog_item_started", "backlog_item_blocked",
                   "backlog_item_done", "backlog_item_cancelled"):
        bid = event.backlog_id or event.payload.get("backlog_id", "unknown")
        state_map = {
            "backlog_item_started": m.BacklogItemState.IN_PROGRESS,
            "backlog_item_blocked": m.BacklogItemState.BLOCKED,
            "backlog_item_done": m.BacklogItemState.DONE,
            "backlog_item_cancelled": m.BacklogItemState.CANCELLED,
        }
        new_state = state_map[etype]
        if bid in backlog_states:
            existing = backlog_states[bid]
            data = existing.model_dump()
            data.update(
                state=new_state,
                updated_at=event.timestamp,
            )
            backlog_states[bid] = m.BacklogItemStateSnapshot(**data)
        else:
            backlog_states[bid] = m.BacklogItemStateSnapshot(
                backlog_id=bid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                state=new_state,
                created_at=event.timestamp,
                updated_at=event.timestamp,
            )

    # ── Approval state transitions ─────────────────────────────────────────
    elif etype == "approval_requested":
        aid = event.payload.get("approval_id", "unknown")
        approval_states[aid] = m.ApprovalStateSnapshot(
            approval_id=aid,
            project_id=project_id,
            launch_id=event.launch_id,
            task_id=event.task_id,
            backlog_id=event.backlog_id,
            state=m.ApprovalState.PENDING,
            requested_by=event.payload.get("requested_by"),
            requested_at=event.timestamp,
            summary=event.payload.get("summary"),
        )
    elif etype in ("approval_granted", "approval_denied", "approval_expired"):
        aid = event.payload.get("approval_id", "unknown")
        state_map = {
            "approval_granted": m.ApprovalState.APPROVED,
            "approval_denied": m.ApprovalState.REJECTED,
            "approval_expired": m.ApprovalState.EXPIRED,
        }
        new_state = state_map[etype]
        if aid in approval_states:
            existing = approval_states[aid]
            data = existing.model_dump()
            data.update(
                state=new_state,
                resolved_by=event.payload.get("resolved_by"),
                resolved_at=event.timestamp,
            )
            approval_states[aid] = m.ApprovalStateSnapshot(**data)
        else:
            approval_states[aid] = m.ApprovalStateSnapshot(
                approval_id=aid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                backlog_id=event.backlog_id,
                state=new_state,
                resolved_by=event.payload.get("resolved_by"),
                resolved_at=event.timestamp,
            )

    # ── Evidence state transitions ─────────────────────────────────────────
    elif etype == "evidence_requested":
        eid = event.payload.get("evidence_id", "unknown")
        evidence_states[eid] = m.EvidenceStateSnapshot(
            evidence_id=eid,
            project_id=project_id,
            launch_id=event.launch_id,
            task_id=event.task_id,
            backlog_id=event.backlog_id,
            state=m.EvidenceState.PENDING,
            source_path=event.payload.get("source_path"),
            collected_at=event.timestamp,
        )
    elif etype == "evidence_collected":
        eid = event.payload.get("evidence_id", "unknown")
        if eid in evidence_states:
            existing = evidence_states[eid]
            data = existing.model_dump()
            data.update(
                state=m.EvidenceState.COLLECTED,
                collected_at=event.timestamp,
            )
            evidence_states[eid] = m.EvidenceStateSnapshot(**data)
        else:
            evidence_states[eid] = m.EvidenceStateSnapshot(
                evidence_id=eid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                backlog_id=event.backlog_id,
                state=m.EvidenceState.COLLECTED,
                collected_at=event.timestamp,
            )
    elif etype == "evidence_verified":
        eid = event.payload.get("evidence_id", "unknown")
        if eid in evidence_states:
            existing = evidence_states[eid]
            data = existing.model_dump()
            data.update(
                state=m.EvidenceState.VERIFIED,
                verified_at=event.timestamp,
                content_hash=event.payload.get("content_hash"),
            )
            evidence_states[eid] = m.EvidenceStateSnapshot(**data)
        else:
            evidence_states[eid] = m.EvidenceStateSnapshot(
                evidence_id=eid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                backlog_id=event.backlog_id,
                state=m.EvidenceState.VERIFIED,
                verified_at=event.timestamp,
                content_hash=event.payload.get("content_hash"),
            )
    elif etype == "evidence_failed":
        eid = event.payload.get("evidence_id", "unknown")
        if eid in evidence_states:
            existing = evidence_states[eid]
            data = existing.model_dump()
            data.update(state=m.EvidenceState.FAILED)
            evidence_states[eid] = m.EvidenceStateSnapshot(**data)
        else:
            evidence_states[eid] = m.EvidenceStateSnapshot(
                evidence_id=eid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                backlog_id=event.backlog_id,
                state=m.EvidenceState.FAILED,
            )

    # ── Promotion state transitions ────────────────────────────────────────
    elif etype == "promotion_requested":
        pid = event.payload.get("promotion_id", "unknown")
        promotion_states[pid] = m.PromotionStateSnapshot(
            promotion_id=pid,
            project_id=project_id,
            launch_id=event.launch_id,
            task_id=event.task_id,
            backlog_id=event.backlog_id,
            state=m.PromotionState.NOT_STARTED,
            requested_by=event.payload.get("requested_by"),
            requested_at=event.timestamp,
            target_ref=event.payload.get("target_ref"),
        )
    elif etype == "promotion_started":
        pid = event.payload.get("promotion_id", "unknown")
        if pid in promotion_states:
            existing = promotion_states[pid]
            data = existing.model_dump()
            data.update(state=m.PromotionState.IN_PROGRESS)
            promotion_states[pid] = m.PromotionStateSnapshot(**data)
        else:
            promotion_states[pid] = m.PromotionStateSnapshot(
                promotion_id=pid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                backlog_id=event.backlog_id,
                state=m.PromotionState.IN_PROGRESS,
            )
    elif etype in ("promotion_approved", "promotion_rejected", "promotion_deployed"):
        pid = event.payload.get("promotion_id", "unknown")
        state_map = {
            "promotion_approved": m.PromotionState.APPROVED,
            "promotion_rejected": m.PromotionState.REJECTED,
            "promotion_deployed": m.PromotionState.DEPLOYED,
        }
        new_state = state_map[etype]
        if pid in promotion_states:
            existing = promotion_states[pid]
            kwargs = existing.model_dump()
            kwargs.update(
                state=new_state,
                approved_at=event.timestamp if etype == "promotion_approved" else existing.approved_at,
                approved_by=event.payload.get("approved_by") if etype == "promotion_approved" else existing.approved_by,
                deployed_at=event.timestamp if etype == "promotion_deployed" else existing.deployed_at,
            )
            promotion_states[pid] = m.PromotionStateSnapshot(**kwargs)
        else:
            promotion_states[pid] = m.PromotionStateSnapshot(
                promotion_id=pid,
                project_id=project_id,
                launch_id=event.launch_id,
                task_id=event.task_id,
                backlog_id=event.backlog_id,
                state=new_state,
                approved_by=event.payload.get("approved_by") if etype == "promotion_approved" else None,
                approved_at=event.timestamp if etype in ("promotion_approved", "promotion_deployed") else None,
                deployed_at=event.timestamp if etype == "promotion_deployed" else None,
                target_ref=event.payload.get("target_ref"),
            )

    # ── Context ingestion events do not mutate projection state ──────────
    # but are preserved in the event list for traceability.


# ── Module-level convenience (for tests and CLI) ─────────────────────────────

_default_store: Optional[MissionControlStore] = None


def get_store() -> MissionControlStore:
    """Return the module-global store (created on first call)."""
    global _default_store
    if _default_store is None:
        _default_store = MissionControlStore()
    return _default_store
