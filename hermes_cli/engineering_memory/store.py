"""Local event-sourced persistence for Hermes Structured Engineering Memory.

File layout under ``$HERMES_HOME/engineering_memory/``::

  meta.json
  events.jsonl
  projects/
    {project_id}/
      events.jsonl

The append-only lifecycle journal is canonical. Current memory state is
deterministically reconstructed by replaying lifecycle events.

Design guarantees:

- strict project isolation
- fail-closed JSON and schema validation
- process and cross-process write locking
- pre-write serialization validation
- global and project-scoped journals
- idempotent append support
- deterministic replay and snapshots
- no destructive mutation of historical records
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from hermes_cli.engineering_memory import models as m


try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


_PROCESS_LOCKS: Dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


# ── Paths ────────────────────────────────────────────────────────────────────

def _engineering_memory_root() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "engineering_memory"


def engineering_memory_root() -> Path:
    return _engineering_memory_root()


def meta_path(*, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _engineering_memory_root()
    return base / "meta.json"


def event_log_path(*, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _engineering_memory_root()
    return base / "events.jsonl"


def project_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    safe = _safe_id(project_id)
    base = root if root is not None else _engineering_memory_root()
    return base / "projects" / safe


def project_event_log_path(
    project_id: str,
    *,
    root: Optional[Path] = None,
) -> Path:
    return project_dir(project_id, root=root) / "events.jsonl"


def _safe_id(identifier: Any) -> str:
    """Reject path traversal and unsafe filesystem identifiers."""
    if not isinstance(identifier, str):
        raise ValueError(f"unsafe identifier: {identifier!r}")

    if (
        not identifier
        or ".." in identifier
        or "/" in identifier
        or "\\" in identifier
        or "\x00" in identifier
    ):
        raise ValueError(f"unsafe identifier: {identifier!r}")

    return identifier


# ── JSON helpers ─────────────────────────────────────────────────────────────

def _json_dumps(
    data: dict,
    *,
    indent: Optional[int] = 2,
    sort_keys: bool = True,
) -> str:
    return json.dumps(
        data,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=False,
    )


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = _preserve_mode(path)

    descriptor, temporary_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".write_",
        suffix=".tmp",
    )

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_json_dumps(data))
            handle.flush()
            os.fsync(handle.fileno())

        if original_mode is not None:
            os.chmod(temporary_path, original_mode)

        os.replace(temporary_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary_path)
        raise


def _preserve_mode(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None


def _load_json_safe(path: Path) -> Optional[dict]:
    """Load one JSON object, failing closed on malformed existing files."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc

    if not raw:
        return None

    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")

    return value


def _load_line_records(path: Path) -> Iterator[dict]:
    """Yield JSONL objects and reject every malformed or non-object line."""
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ValueError(
                f"malformed JSONL line {line_number} in {path}: {exc}"
            ) from exc

        if not isinstance(value, dict):
            raise ValueError(
                f"expected JSON object at line {line_number} in {path}"
            )

        yield value


def _event_journal_line(event: m.MemoryEvent) -> str:
    """Validate and serialize an event before mutating any journal."""
    _safe_id(event.project_id)
    _safe_id(event.memory_id)
    _safe_id(event.event_id)

    try:
        data = event.model_dump(mode="json")
        return _json_dumps(data, indent=None) + "\n"
    except Exception as exc:
        raise ValueError(
            f"memory event {event.event_id!r} is not JSON serializable"
        ) from exc


def _process_lock_for(root: Path) -> threading.RLock:
    key = str(root.resolve(strict=False))

    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


# ── Store ────────────────────────────────────────────────────────────────────

class EngineeringMemoryStore:
    """Persistent event store for Structured Engineering Memory."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or _engineering_memory_root()
        self._ensure_root()

    @property
    def root(self) -> Path:
        return self._root

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Serialize sequence allocation and journal writes."""
        process_lock = _process_lock_for(self._root)

        with process_lock:
            lock_path = self._root / ".write.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)

            with lock_path.open("a", encoding="utf-8") as lock_handle:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)

                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = meta_path(root=self._root)

        if not path.exists():
            _atomic_write(
                path,
                {
                    "schema_version": m.CURRENT_SCHEMA_VERSION,
                    "created_at": m._utc_now(),
                    "version": 1,
                },
            )
            return

        metadata = _load_json_safe(path)
        if metadata is None:
            raise ValueError(f"empty engineering memory metadata file: {path}")

        schema_version = metadata.get("schema_version")
        if schema_version not in m.SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"schema version {schema_version} not supported "
                f"(supported: {sorted(m.SUPPORTED_SCHEMA_VERSIONS)})"
            )

    # ── Journal mutation ───────────────────────────────────────────────────

    def append_event(self, event: m.MemoryEvent) -> m.MemoryEvent:
        """Append one event to global and project journals."""
        with self.write_lock():
            return self._append_event_unlocked(event)

    def append_event_once(
        self,
        event: m.MemoryEvent,
    ) -> Optional[m.MemoryEvent]:
        """Append unless event identity or source idempotency key exists."""
        with self.write_lock():
            existing_events = list(self.iter_events(project_id=event.project_id))

            if any(existing.event_id == event.event_id for existing in existing_events):
                return None

            source_key = event.payload.get("source_idempotency_key")
            if source_key is not None:
                for existing in existing_events:
                    if existing.payload.get("source_idempotency_key") == source_key:
                        return None

            return self._append_event_unlocked(event)

    def append_events(
        self,
        events: List[m.MemoryEvent],
    ) -> List[m.MemoryEvent]:
        """Append a batch with deterministic per-project sequence assignment."""
        if not events:
            return []

        with self.write_lock():
            prepared: List[m.MemoryEvent] = []
            project_counts: Dict[str, int] = {}

            for event in events:
                if event.project_id not in project_counts:
                    project_counts[event.project_id] = self.event_count(
                        project_id=event.project_id
                    )

                if event.sequence == 0:
                    project_counts[event.project_id] += 1
                    event.sequence = project_counts[event.project_id]

                prepared.append(event)

            lines = [_event_journal_line(event) for event in prepared]
            self._write_prevalidated_events(prepared, lines)
            return prepared

    def _append_event_unlocked(self, event: m.MemoryEvent) -> m.MemoryEvent:
        """Append one event while the caller holds ``write_lock``."""
        if event.sequence == 0:
            event.sequence = self.event_count(project_id=event.project_id) + 1

        line = _event_journal_line(event)
        self._write_prevalidated_events([event], [line])
        return event

    def _write_prevalidated_events(
        self,
        events: List[m.MemoryEvent],
        lines: List[str],
    ) -> None:
        """Write prevalidated lines to global and project journals."""
        global_path = event_log_path(root=self._root)
        global_path.parent.mkdir(parents=True, exist_ok=True)

        with global_path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

        project_batches: Dict[str, List[str]] = {}
        for event, line in zip(events, lines):
            project_batches.setdefault(event.project_id, []).append(line)

        for project_id, project_lines in project_batches.items():
            path = project_event_log_path(project_id, root=self._root)
            path.parent.mkdir(parents=True, exist_ok=True)

            with path.open("a", encoding="utf-8") as handle:
                for line in project_lines:
                    handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    # ── Journal reads ──────────────────────────────────────────────────────

    def iter_events(
        self,
        project_id: Optional[str] = None,
    ) -> Iterator[m.MemoryEvent]:
        """Replay the global or isolated project journal."""
        if project_id is None:
            path = event_log_path(root=self._root)
        else:
            _safe_id(project_id)
            path = project_event_log_path(project_id, root=self._root)

        for data in _load_line_records(path):
            try:
                event = m.MemoryEvent(**data)
            except Exception as exc:
                raise ValueError(
                    f"invalid engineering memory event in {path}: {exc}"
                ) from exc

            if project_id is not None and event.project_id != project_id:
                raise ValueError(
                    f"project isolation violation: event {event.event_id} "
                    f"belongs to {event.project_id}, not {project_id}"
                )

            yield event

    def event_count(self, project_id: Optional[str] = None) -> int:
        if project_id is None:
            path = event_log_path(root=self._root)
        else:
            _safe_id(project_id)
            path = project_event_log_path(project_id, root=self._root)

        if not path.exists():
            return 0

        try:
            with path.open(encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc

    def list_project_ids(self) -> List[str]:
        projects_path = self._root / "projects"
        if not projects_path.exists():
            return []

        project_ids: List[str] = []

        for entry in projects_path.iterdir():
            if not entry.is_dir():
                continue

            try:
                _safe_id(entry.name)
            except ValueError:
                continue

            if (entry / "events.jsonl").exists():
                project_ids.append(entry.name)

        return sorted(project_ids)

    # ── Deterministic replay ───────────────────────────────────────────────

    def build_snapshot(
        self,
        project_id: str,
        *,
        generated_by: Optional[str] = None,
    ) -> m.MemorySnapshot:
        """Reconstruct current project memory from lifecycle events."""
        _safe_id(project_id)

        events = sorted(
            self.iter_events(project_id=project_id),
            key=lambda event: event.stable_sort_key(),
        )

        memories: Dict[str, m.MemoryRecord] = {}

        for event in events:
            raw_memory = event.payload.get("memory")
            if raw_memory is None:
                raise ValueError(
                    f"memory event {event.event_id} lacks payload.memory"
                )

            if not isinstance(raw_memory, dict):
                raise ValueError(
                    f"memory event {event.event_id} payload.memory must be an object"
                )

            try:
                memory = m.MemoryRecord(**raw_memory)
            except Exception as exc:
                raise ValueError(
                    f"invalid memory payload in event {event.event_id}: {exc}"
                ) from exc

            if memory.project_id != project_id:
                raise ValueError(
                    f"project isolation violation: memory {memory.memory_id} "
                    f"belongs to {memory.project_id}, not {project_id}"
                )

            if memory.memory_id != event.memory_id:
                raise ValueError(
                    f"memory identity mismatch in event {event.event_id}: "
                    f"{event.memory_id} != {memory.memory_id}"
                )

            memories[memory.memory_id] = memory

        ordered_memories = sorted(
            memories.values(),
            key=lambda memory: (memory.created_at, memory.memory_id),
        )

        return m.MemorySnapshot(
            version=len(events),
            generated_by=generated_by,
            project_id=project_id,
            event_count=len(events),
            memories=ordered_memories,
        )

    def get_memory(
        self,
        project_id: str,
        memory_id: str,
    ) -> Optional[m.MemoryRecord]:
        _safe_id(project_id)
        _safe_id(memory_id)

        snapshot = self.build_snapshot(project_id)
        for memory in snapshot.memories:
            if memory.memory_id == memory_id:
                return memory

        return None

    def list_memories(
        self,
        project_id: str,
        *,
        status: Optional[m.MemoryStatus] = None,
        memory_type: Optional[m.MemoryType] = None,
        include_inactive: bool = False,
    ) -> List[m.MemoryRecord]:
        memories = self.build_snapshot(project_id).memories

        if status is not None:
            memories = [memory for memory in memories if memory.status == status]
        elif not include_inactive:
            memories = [
                memory
                for memory in memories
                if memory.status in {
                    m.MemoryStatus.CANDIDATE,
                    m.MemoryStatus.VERIFIED,
                }
            ]

        if memory_type is not None:
            memories = [
                memory
                for memory in memories
                if memory.memory_type == memory_type
            ]

        return memories


_GLOBAL_STORE: Optional[EngineeringMemoryStore] = None


def get_store() -> EngineeringMemoryStore:
    global _GLOBAL_STORE

    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = EngineeringMemoryStore()

    return _GLOBAL_STORE
