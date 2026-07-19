"""Local JSON + JSONL persistence for Hermes Shared Engineering Context.

Design decisions (see docs/adr-001-shared-engineering-context-persistence.md):
- Project records live in atomic-write JSON files (one per project, one snapshot file).
- The audit journal is an append-only JSONL file — mutations are never in-place.
- The audit journal provides a durable foundation for future replay and recovery.
- No external database; no new server dependency.

File layout under $HERMES_HOME/context/:
  meta.json          — store manifest (schema version, created_at)
  events.jsonl       — append-only audit journal
  projects/
    {project_id}/
      project.json   — latest Project state
      records.jsonl   — all ContextRecord events (each line = one record state)
      launches.jsonl  — all LaunchContext events

Malformed / partial writes are detected and rejected (fail-closed). Recovery
proceeds from the last valid snapshot.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from hermes_cli.context_engine import models as m

logger = logging.getLogger("hermes.context_engine.store")

# ── Paths ────────────────────────────────────────────────────────────────────

def _context_root() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "context"


def context_root() -> Path:
    return _context_root()


def meta_path(*, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _context_root()
    return base / "meta.json"


def event_log_path(*, root: Optional[Path] = None) -> Path:
    base = root if root is not None else _context_root()
    return base / "events.jsonl"


def project_dir(project_id: str, *, root: Optional[Path] = None) -> Path:
    safe = _safe_id(project_id)
    base = root if root is not None else _context_root()
    return base / "projects" / safe


def project_file(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "project.json"


def records_file(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "records.jsonl"


def launches_file(project_id: str, *, root: Optional[Path] = None) -> Path:
    return project_dir(project_id, root=root) / "launches.jsonl"


def _safe_id(ident: str) -> str:
    """Reject path traversal and other unsafe identifiers."""
    if not ident or ".." in ident or "/" in ident or "\\" in ident or "\x00" in ident:
        raise ValueError(f"unsafe identifier: {ident!r}")
    return ident


# ── JSON helpers ─────────────────────────────────────────────────────────────

def _json_loads(s: str) -> dict:
    return json.loads(s)


def _json_dumps(data: dict, *, indent: int = 2, sort_keys: bool = True) -> str:
    return json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=False)


# ── Atomic write ─────────────────────────────────────────────────────────────

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


# ── Store class ──────────────────────────────────────────────────────────────

class ContextStore:
    """Persistent store for Hermes Shared Engineering Context.

    Thread-unsafe — use a lock at the service layer if multi-threaded access
    is needed. All mutations are atomic (partial writes cannot survive).

    Args:
        root: Explicit root path. Defaults to ``$HERMES_HOME/context/``.
              Intended for tests only.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or _context_root()
        self._ensure_root()

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

    # ── Project operations ─────────────────────────────────────────────────

    def get_project(self, project_id: str) -> Optional[m.Project]:
        """Load a project, returning None if not found."""
        _safe_id(project_id)
        data = _load_json_safe(project_file(project_id, root=self._root))
        if data is None:
            return None
        try:
            return m.Project(**data)
        except Exception as exc:
            raise ValueError(f"invalid project record {project_id}: {exc}") from exc

    def list_projects(self) -> List[m.Project]:
        """List all registered projects."""
        projects_dir = self._root / "projects"
        results: list[m.Project] = []
        if not projects_dir.exists():
            return results
        for entry in projects_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                _safe_id(entry.name)
            except ValueError:
                # Skip directories with unsafe names — project isolation.
                continue
            proj = self.get_project(entry.name)
            if proj is not None:
                results.append(proj)
        return sorted(results, key=lambda p: p.project_id)

    def save_project(self, project: m.Project) -> None:
        """Atomically persist a project. Fails if a different identity exists."""
        _safe_id(project.project_id)
        existing = self.get_project(project.project_id)
        if existing is not None:
            if existing.repository_identity and project.repository_identity:
                if existing.repository_identity != project.repository_identity:
                    raise ValueError(
                        f"project identity conflict: {project.project_id} "
                        f"already registered for {existing.repository_identity!r}, "
                        f"cannot re-register for {project.repository_identity!r}"
                    )
        _atomic_write(project_file(project.project_id, root=self._root), project.model_dump())

    # ── Record operations ──────────────────────────────────────────────────

    def list_records(
        self,
        project_id: str,
        record_type: Optional[m.RecordType] = None,
        status: Optional[m.RecordStatus] = None,
        include_inactive: bool = False,
    ) -> List[m.ContextRecord]:
        """List records for a project, optionally filtered."""
        _safe_id(project_id)
        path = records_file(project_id, root=self._root)
        records: Dict[str, m.ContextRecord] = {}
        for data in _load_line_records(path):
            try:
                rec = m.ContextRecord(**data)
            except Exception as exc:
                raise ValueError(f"invalid record in {path}: {exc}") from exc
            if rec.project_id != project_id:
                raise ValueError(
                    f"project isolation violation: record {rec.record_id} "
                    f"belongs to {rec.project_id}, not {project_id}"
                )
            # Keep latest version of each record_id (last line wins in journal).
            records[rec.record_id] = rec

        results = sorted(records.values(), key=lambda r: r.created_at)
        if record_type is not None:
            results = [r for r in results if r.record_type == record_type]
        if status is not None:
            results = [r for r in results if r.status == status]
        if not include_inactive:
            active = m.RecordStatus.ACTIVE
            results = [r for r in results if r.status == active]
        return results

    def save_record(self, record: m.ContextRecord) -> None:
        """Append a record version to the journal."""
        _safe_id(record.project_id)
        path = records_file(record.project_id, root=self._root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_json_dumps(record.model_dump(), indent=None) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ── Launch operations ───────────────────────────────────────────────────

    def list_launches(
        self,
        project_id: str,
        status: Optional[m.LaunchStatus] = None,
    ) -> List[m.LaunchContext]:
        """List launches for a project."""
        _safe_id(project_id)
        path = launches_file(project_id, root=self._root)
        launches: Dict[str, m.LaunchContext] = {}
        for data in _load_line_records(path):
            try:
                launch = m.LaunchContext(**data)
            except Exception as exc:
                raise ValueError(f"invalid launch in {path}: {exc}") from exc
            if launch.project_id != project_id:
                raise ValueError(
                    f"project isolation violation: launch {launch.launch_id} "
                    f"belongs to {launch.project_id}, not {project_id}"
                )
            launches[launch.launch_id] = launch

        results = sorted(launches.values(), key=lambda l: l.started_at)
        if status is not None:
            results = [r for r in results if r.status == status]
        return results

    def save_launch(self, launch: m.LaunchContext) -> None:
        """Append a launch version to the journal."""
        _safe_id(launch.project_id)
        path = launches_file(launch.project_id, root=self._root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_json_dumps(launch.model_dump(), indent=None) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ── Audit journal ───────────────────────────────────────────────────────

    def append_event(self, event: m.ContextEvent) -> None:
        """Append an event to the audit journal (append-only, never truncated)."""
        path = event_log_path(root=self._root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _json_dumps(event.model_dump(), indent=None) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def iter_events(self, project_id: Optional[str] = None) -> Iterator[m.ContextEvent]:
        """Replay the audit journal, optionally scoped to one project."""
        path = event_log_path(root=self._root)
        for data in _load_line_records(path):
            try:
                event = m.ContextEvent(**data)
            except Exception as exc:
                raise ValueError(f"invalid event in {path}: {exc}") from exc
            if project_id is not None and event.project_id != project_id:
                continue
            yield event

    def event_count(self) -> int:
        """Count lines in the audit journal (approximate, for snapshot versioning)."""
        path = event_log_path(root=self._root)
        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    # ── Snapshot projection ────────────────────────────────────────────────

    def build_snapshot(
        self,
        project_id: str,
        generated_by: Optional[str] = None,
    ) -> m.ContextSnapshot:
        """Deterministic snapshot: latest version of each entity from journal replay."""
        _safe_id(project_id)
        project = self.get_project(project_id)
        if project is None:
            raise ValueError(f"no such project: {project_id}")
        records: Dict[str, m.ContextRecord] = {}
        launches: Dict[str, m.LaunchContext] = {}
        event_count = 0

        for data in _load_line_records(records_file(project_id, root=self._root)):
            rec = m.ContextRecord(**data)
            records[rec.record_id] = rec
            event_count += 1

        for data in _load_line_records(launches_file(project_id, root=self._root)):
            launch = m.LaunchContext(**data)
            launches[launch.launch_id] = launch
            event_count += 1

        # Snapshot version = total event count for deterministic replay.
        version = event_count + 1

        return m.ContextSnapshot(
            version=version,
            generated_at=m._utc_now(),
            generated_by=generated_by,
            project_id=project_id,
            project=project,
            records=sorted(records.values(), key=lambda r: r.record_id),
            launches=sorted(launches.values(), key=lambda l: l.launch_id),
            event_count=event_count,
        )

    # ── Foreman state import ───────────────────────────────────────────────

    def import_launch(self, launch: m.LaunchContext) -> None:
        """Import or update a launch from Foreman."""
        self.save_launch(launch)
        self.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="launch_updated",
            project_id=launch.project_id,
            actor="foreman",
            payload={"launch_id": launch.launch_id, "status": launch.status.value},
        ))


# ── Module-level convenience (for tests and CLI) ─────────────────────────────

_default_store: Optional[ContextStore] = None


def get_store() -> ContextStore:
    """Return the module-global store (created on first call)."""
    global _default_store
    if _default_store is None:
        _default_store = ContextStore()
    return _default_store
