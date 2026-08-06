"""Minimal, persistent, file-backed pending-task queue for Titan's reflection cycle.

Required per the dynamic-runtime design: "persistent queue and scheduler
state". Each pending task is one small JSON file in a directory -- crash-safe
(atomic write via a temp file + ``os.replace``), inspectable with ordinary
shell tools, and cheap for a systemd ``.path`` unit to watch for changes to
trigger an immediate, event-driven wake (see ``deploy/titan/titan-reflection.path``).

This is not a general-purpose job queue (no priority, no visibility timeout,
no retry bookkeeping of its own) -- it is deliberately just durable storage
for "what is Titan supposed to look at next," matching the narrow scope this
feature actually needs. It does not replace or duplicate any existing queue;
none was found for Titan (only Prime's fleet registry / admission modules
and the unrelated generic ``cron`` job store exist today).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple


class TaskQueueError(ValueError):
    """The task queue is misconfigured or a task payload is invalid."""


@dataclass(frozen=True, slots=True)
class QueueTask:
    task_id: str
    task_type: str
    context_length_tokens: int
    input_reference: str
    privacy_sensitive: bool = False
    submitted_at: int = 0

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise TaskQueueError("task_id must not be blank")
        if not self.task_type.strip():
            raise TaskQueueError("task_type must not be blank")
        if self.context_length_tokens < 0:
            raise TaskQueueError("context_length_tokens must not be negative")
        if not self.input_reference.strip():
            raise TaskQueueError("input_reference must not be blank")


class PersistentTaskQueue:
    """A directory of one JSON file per pending task."""

    def __init__(self, directory: Path) -> None:
        directory = Path(directory)
        if not directory.is_absolute():
            raise TaskQueueError("task queue directory must be an absolute path")
        self._directory = directory

    def enqueue(self, task: QueueTask) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._directory / f"{task.task_id}.json"
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(asdict(task), sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)

    def list_pending(self) -> Tuple[QueueTask, ...]:
        if not self._directory.exists():
            return ()
        tasks = []
        for entry in sorted(self._directory.glob("*.json")):
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
                tasks.append(QueueTask(**data))
            except (OSError, json.JSONDecodeError, TypeError, TaskQueueError):
                continue  # a malformed task file is skipped, never crashes the cycle
        return tuple(sorted(tasks, key=lambda item: item.submitted_at))

    def dequeue(self, task_id: str) -> None:
        path = self._directory / f"{task_id}.json"
        path.unlink(missing_ok=True)

    def is_empty(self) -> bool:
        return len(self.list_pending()) == 0


def new_task_id() -> str:
    return f"titan-task-{uuid.uuid4().hex}"
