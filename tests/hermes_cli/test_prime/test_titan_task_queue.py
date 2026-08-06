from __future__ import annotations

import pytest

from hermes_cli.prime.titan_task_queue import (
    PersistentTaskQueue,
    QueueTask,
    TaskQueueError,
    new_task_id,
)


def _task(**overrides) -> QueueTask:
    fields = dict(
        task_id="task-1",
        task_type="summary",
        context_length_tokens=100,
        input_reference="do the thing",
        submitted_at=100,
    )
    fields.update(overrides)
    return QueueTask(**fields)


def test_enqueue_and_list_pending(tmp_path) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue")
    queue.enqueue(_task())
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].task_id == "task-1"


def test_list_pending_empty_directory(tmp_path) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue")
    assert queue.list_pending() == ()
    assert queue.is_empty() is True


def test_dequeue_removes_task(tmp_path) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue")
    queue.enqueue(_task())
    queue.dequeue("task-1")
    assert queue.is_empty() is True


def test_dequeue_missing_task_does_not_raise(tmp_path) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue")
    queue.dequeue("does-not-exist")  # must not raise


def test_pending_ordered_by_submitted_at(tmp_path) -> None:
    queue = PersistentTaskQueue(tmp_path / "queue")
    queue.enqueue(_task(task_id="second", submitted_at=200))
    queue.enqueue(_task(task_id="first", submitted_at=100))
    pending = queue.list_pending()
    assert [t.task_id for t in pending] == ["first", "second"]


def test_malformed_task_file_is_skipped_not_crashed(tmp_path) -> None:
    directory = tmp_path / "queue"
    directory.mkdir(parents=True)
    (directory / "bad.json").write_text("{not valid json", encoding="utf-8")
    queue = PersistentTaskQueue(directory)
    assert queue.list_pending() == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(task_id=""),
        dict(task_type=""),
        dict(context_length_tokens=-1),
        dict(input_reference=""),
    ],
)
def test_task_rejects_invalid_fields(kwargs) -> None:
    defaults = dict(
        task_id="t",
        task_type="summary",
        context_length_tokens=1,
        input_reference="x",
    )
    defaults.update(kwargs)
    with pytest.raises(TaskQueueError):
        QueueTask(**defaults)


def test_queue_rejects_relative_directory() -> None:
    with pytest.raises(TaskQueueError):
        PersistentTaskQueue("relative/queue")  # type: ignore[arg-type]


def test_new_task_id_is_unique() -> None:
    assert new_task_id() != new_task_id()
