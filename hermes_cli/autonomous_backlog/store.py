"""Append-only persistence for the governed autonomous backlog."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from hermes_cli.autonomous_backlog import models as m
from hermes_constants import get_hermes_home


def _default_root() -> Path:
    """Return the active Hermes profile's autonomous backlog root."""
    return Path(get_hermes_home()) / "autonomous_backlog"

_STORE_VERSION = 1

_PROCESS_LOCK = threading.RLock()


# ── Path helpers ─────────────────────────────────────────────────────────────

def store_root(root: Optional[Path] = None) -> Path:
    """Return the configured autonomous backlog storage root."""
    return Path(root) if root is not None else _default_root()


def meta_path(root: Optional[Path] = None) -> Path:
    return store_root(root) / "meta.json"


def event_log_path(root: Optional[Path] = None) -> Path:
    return store_root(root) / "events.jsonl"


def projects_path(root: Optional[Path] = None) -> Path:
    return store_root(root) / "projects"


def project_path(
    project_id: str,
    root: Optional[Path] = None,
) -> Path:
    _safe_id(project_id)
    return projects_path(root) / project_id


def project_event_log_path(
    project_id: str,
    root: Optional[Path] = None,
) -> Path:
    return project_path(project_id, root) / "events.jsonl"


def lock_path(root: Optional[Path] = None) -> Path:
    return store_root(root) / ".write.lock"


# ── Validation and serialization helpers ────────────────────────────────────

def _safe_id(value: str) -> str:
    """Reject identifiers that could escape the storage root."""
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"unsafe identifier: {value!r}")

    return value


def _event_journal_line(event: m.BacklogEvent) -> str:
    """Serialize one event as canonical JSONL."""
    return (
        json.dumps(
            event.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )


def _load_line_records(path: Path) -> Iterator[dict]:
    """Load JSON objects from a journal and fail closed on corruption."""
    if not path.exists():
        return

    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue

                try:
                    data = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"malformed JSONL line {line_number} in {path}: {exc}"
                    ) from exc

                if not isinstance(data, dict):
                    raise ValueError(
                        f"JSONL line {line_number} in {path} "
                        "must contain an object"
                    )

                yield data
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


# ── Store ────────────────────────────────────────────────────────────────────

class AutonomousBacklogStore:
    """Append-only event store for governed autonomous backlog state."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = store_root(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._initialise_manifest()

    @property
    def root(self) -> Path:
        return self._root

    def _initialise_manifest(self) -> None:
        path = meta_path(root=self._root)

        if path.exists():
            try:
                metadata = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"invalid autonomous backlog manifest {path}: {exc}"
                ) from exc

            if not isinstance(metadata, dict):
                raise ValueError(
                    f"invalid autonomous backlog manifest {path}: "
                    "expected object"
                )

            schema_version = metadata.get("schema_version")
            if schema_version not in m.SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError(
                    f"schema version {schema_version} not supported; "
                    f"supported versions: "
                    f"{sorted(m.SUPPORTED_SCHEMA_VERSIONS)}"
                )

            version = metadata.get("version")
            if version != _STORE_VERSION:
                raise ValueError(
                    f"autonomous backlog store version {version} "
                    f"not supported"
                )

            return

        metadata = {
            "schema_version": m.CURRENT_SCHEMA_VERSION,
            "version": _STORE_VERSION,
        }

        try:
            path.write_text(
                json.dumps(
                    metadata,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise ValueError(
                f"cannot initialise autonomous backlog manifest {path}: {exc}"
            ) from exc

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Serialize journal writes within this Hermes process.

        The lock file also leaves a visible marker for future process-level
        locking without weakening the current write discipline.
        """
        path = lock_path(root=self._root)
        path.parent.mkdir(parents=True, exist_ok=True)

        with _PROCESS_LOCK:
            try:
                path.touch(exist_ok=True)
            except OSError as exc:
                raise ValueError(
                    f"cannot create autonomous backlog lock {path}: {exc}"
                ) from exc

            yield

    # ── Journal writes ──────────────────────────────────────────────────────

    def append_event(self, event: m.BacklogEvent) -> m.BacklogEvent:
        """Append one event with deterministic project sequence allocation."""
        with self.write_lock():
            return self._append_event_unlocked(event)

    def append_event_once(
        self,
        event: m.BacklogEvent,
    ) -> Optional[m.BacklogEvent]:
        """Append unless its event ID or idempotency key already exists."""
        with self.write_lock():
            for existing in self.iter_events(
                project_id=event.project_id
            ):
                if existing.event_id == event.event_id:
                    return None

                if (
                    event.idempotency_key is not None
                    and existing.idempotency_key == event.idempotency_key
                ):
                    return None

            return self._append_event_unlocked(event)

    def append_events(
        self,
        events: List[m.BacklogEvent],
    ) -> List[m.BacklogEvent]:
        """Append a batch with deterministic per-project sequences."""
        if not events:
            return []

        with self.write_lock():
            prepared: List[m.BacklogEvent] = []
            project_counts: Dict[str, int] = {}

            for event in events:
                if event.project_id not in project_counts:
                    project_counts[event.project_id] = self.event_count(
                        project_id=event.project_id
                    )

                project_counts[event.project_id] += 1

                if event.sequence != project_counts[event.project_id]:
                    event = event.model_copy(
                        update={
                            "sequence": project_counts[event.project_id],
                        }
                    )

                prepared.append(event)

            lines = [
                _event_journal_line(event)
                for event in prepared
            ]

            self._write_prevalidated_events(prepared, lines)
            return prepared

    def _append_event_unlocked(
        self,
        event: m.BacklogEvent,
    ) -> m.BacklogEvent:
        """Append one event while the caller holds ``write_lock``."""
        next_sequence = (
            self.event_count(project_id=event.project_id) + 1
        )

        if event.sequence != next_sequence:
            event = event.model_copy(
                update={
                    "sequence": next_sequence,
                }
            )

        line = _event_journal_line(event)
        self._write_prevalidated_events([event], [line])
        return event

    def _write_prevalidated_events(
        self,
        events: List[m.BacklogEvent],
        lines: List[str],
    ) -> None:
        """Write validated events to global and project journals."""
        if len(events) != len(lines):
            raise ValueError(
                "event and serialized line counts must match"
            )

        global_path = event_log_path(root=self._root)
        global_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with global_path.open("a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line)

                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ValueError(
                f"cannot append autonomous backlog journal "
                f"{global_path}: {exc}"
            ) from exc

        project_batches: Dict[str, List[str]] = {}

        for event, line in zip(events, lines):
            _safe_id(event.project_id)
            project_batches.setdefault(
                event.project_id,
                [],
            ).append(line)

        for project_id, project_lines in project_batches.items():
            path = project_event_log_path(
                project_id,
                root=self._root,
            )
            path.parent.mkdir(parents=True, exist_ok=True)

            try:
                with path.open("a", encoding="utf-8") as handle:
                    for line in project_lines:
                        handle.write(line)

                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise ValueError(
                    f"cannot append autonomous backlog project journal "
                    f"{path}: {exc}"
                ) from exc

    # ── Journal reads ───────────────────────────────────────────────────────

    def iter_events(
        self,
        project_id: Optional[str] = None,
    ) -> Iterator[m.BacklogEvent]:
        """Replay the global or isolated project journal."""
        if project_id is None:
            path = event_log_path(root=self._root)
        else:
            _safe_id(project_id)
            path = project_event_log_path(
                project_id,
                root=self._root,
            )

        for data in _load_line_records(path):
            try:
                event = m.BacklogEvent(**data)
            except Exception as exc:
                raise ValueError(
                    f"invalid autonomous backlog event in {path}: {exc}"
                ) from exc

            if (
                project_id is not None
                and event.project_id != project_id
            ):
                raise ValueError(
                    f"project isolation violation: event "
                    f"{event.event_id} belongs to "
                    f"{event.project_id}, not {project_id}"
                )

            yield event

    def event_count(
        self,
        project_id: Optional[str] = None,
    ) -> int:
        if project_id is None:
            path = event_log_path(root=self._root)
        else:
            _safe_id(project_id)
            path = project_event_log_path(
                project_id,
                root=self._root,
            )

        if not path.exists():
            return 0

        try:
            with path.open(encoding="utf-8") as handle:
                return sum(
                    1
                    for line in handle
                    if line.strip()
                )
        except OSError as exc:
            raise ValueError(
                f"cannot read {path}: {exc}"
            ) from exc

    def list_project_ids(self) -> List[str]:
        path = projects_path(root=self._root)

        if not path.exists():
            return []

        project_ids: List[str] = []

        for entry in path.iterdir():
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
    ) -> m.BacklogSnapshot:
        """Reconstruct current backlog state from lifecycle events."""
        _safe_id(project_id)

        events = sorted(
            self.iter_events(project_id=project_id),
            key=lambda event: event.stable_sort_key(),
        )

        items: Dict[str, m.BacklogItem] = {}
        versions: Dict[str, int] = {}

        for event in events:
            raw_item = event.payload.get("item")

            if raw_item is None:
                raise ValueError(
                    f"backlog event {event.event_id} lacks payload.item"
                )

            if not isinstance(raw_item, dict):
                raise ValueError(
                    f"backlog event {event.event_id} "
                    "payload.item must be an object"
                )

            try:
                item = m.BacklogItem(**raw_item)
            except Exception as exc:
                raise ValueError(
                    f"invalid backlog item payload in event "
                    f"{event.event_id}: {exc}"
                ) from exc

            if item.project_id != project_id:
                raise ValueError(
                    f"project isolation violation: backlog item "
                    f"{item.item_id} belongs to {item.project_id}, "
                    f"not {project_id}"
                )

            if item.item_id != event.item_id:
                raise ValueError(
                    f"backlog item identity mismatch in event "
                    f"{event.event_id}: "
                    f"{event.item_id} != {item.item_id}"
                )

            if item.version != event.resulting_version:
                raise ValueError(
                    f"backlog item version mismatch in event "
                    f"{event.event_id}: item version {item.version} "
                    f"!= resulting version {event.resulting_version}"
                )

            previous_version = versions.get(item.item_id, 0)

            if event.expected_version is not None:
                if event.expected_version != previous_version:
                    raise ValueError(
                        f"backlog version conflict in event "
                        f"{event.event_id}: expected "
                        f"{event.expected_version}, current "
                        f"{previous_version}"
                    )

            if event.resulting_version != previous_version + 1:
                raise ValueError(
                    f"non-monotonic backlog version in event "
                    f"{event.event_id}: resulting version "
                    f"{event.resulting_version}, expected "
                    f"{previous_version + 1}"
                )

            items[item.item_id] = item
            versions[item.item_id] = item.version

        ordered_items = sorted(
            items.values(),
            key=lambda item: item.item_id,
        )

        return m.BacklogSnapshot(
            version=max(len(events), 1),
            generated_by=generated_by,
            project_id=project_id,
            event_count=len(events),
            items=ordered_items,
        )

    def get_item(
        self,
        project_id: str,
        item_id: str,
    ) -> Optional[m.BacklogItem]:
        """Return one projected backlog item."""
        _safe_id(project_id)
        _safe_id(item_id)

        snapshot = self.build_snapshot(project_id)

        for item in snapshot.items:
            if item.item_id == item_id:
                return item

        return None

    def list_items(
        self,
        project_id: str,
        *,
        status: Optional[m.BacklogStatus] = None,
        priority: Optional[m.BacklogPriority] = None,
        risk_level: Optional[m.BacklogRiskLevel] = None,
        include_terminal: bool = False,
    ) -> List[m.BacklogItem]:
        """Return deterministic filtered backlog items."""
        items = self.build_snapshot(project_id).items

        if status is not None:
            items = [
                item
                for item in items
                if item.status == status
            ]
        elif not include_terminal:
            terminal_statuses = {
                m.BacklogStatus.COMPLETED,
                m.BacklogStatus.CANCELLED,
                m.BacklogStatus.SUPERSEDED,
                m.BacklogStatus.FAILED,
            }

            items = [
                item
                for item in items
                if item.status not in terminal_statuses
            ]

        if priority is not None:
            items = [
                item
                for item in items
                if item.priority == priority
            ]

        if risk_level is not None:
            items = [
                item
                for item in items
                if item.risk_level == risk_level
            ]

        return items


_GLOBAL_STORE: Optional[AutonomousBacklogStore] = None


def get_store() -> AutonomousBacklogStore:
    global _GLOBAL_STORE

    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = AutonomousBacklogStore()

    return _GLOBAL_STORE
