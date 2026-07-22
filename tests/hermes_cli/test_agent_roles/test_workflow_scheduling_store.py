"""Step 8 scheduling persistence and concurrency certification."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.workflow import (
    AuthorizationDecision,
    WorkflowAuthorization,
    WorkflowDecision,
)
from hermes_cli.agent_roles.workflow_scheduling import CoordinationStatus, WorkflowExecutionIntent
from hermes_cli.agent_roles.workflow_scheduling_store import (
    WorkflowSchedulingJournalRecord,
    WorkflowSchedulingStore,
)


def intent(number: int = 1, *, project_id: str = "project_1") -> WorkflowExecutionIntent:
    suffix = str(number)
    authorization = WorkflowAuthorization(
        authorization_id="auth_1",
        workflow_id="workflow_1",
        project_id=project_id,
        expected_version=2,
        decision=WorkflowDecision.ADVANCE,
        disposition=AuthorizationDecision.APPROVED,
        actor="human",
        reason="explicit review authorization",
        timestamp=40,
        to_role_id="reviewer",
    )
    return WorkflowExecutionIntent(
        intent_id=f"intent_{suffix}", version=1, status=CoordinationStatus.SCHEDULED,
        project_id=project_id, workflow_id="workflow_1", workflow_version=3,
        run_id="run_1", node_run_id=f"node_{suffix}", stage_sequence=2,
        assignment_id=f"assignment_{suffix}", plan_id=f"plan_{suffix}",
        role_id="reviewer", agent_id="agent_1", decision=WorkflowDecision.ADVANCE,
        authorization_id="auth_1", authorization=authorization,
        attempt_id=f"attempt_{suffix}", actor_id="coordinator",
        correlation_id="run_1", causation_id="auth_event_1",
        created_at=40 + number, updated_at=40 + number, available_at=40 + number,
    )


def test_store_replay_order_project_isolation_and_restart(tmp_path) -> None:
    root = tmp_path / "scheduling"
    store = WorkflowSchedulingStore(root)
    second, first = intent(2), intent(1)
    store.create(second)
    store.create(first)
    restarted = WorkflowSchedulingStore(root)
    assert restarted.list("project_1") == (first, second)
    assert restarted.list("project_2") == ()
    assert store.journal_path("project_1").stat().st_mode & 0o077 == 0
    assert store.journal_path("project_1").with_suffix(".lock").stat().st_mode & 0o077 == 0


def test_concurrent_claim_has_exactly_one_winner(tmp_path) -> None:
    root = tmp_path / "scheduling"
    WorkflowSchedulingStore(root).create(intent())

    def claim(worker):
        try:
            return WorkflowSchedulingStore(root).claim(
                "project_1", "intent_1", claimed_by=worker,
                timestamp=50, lease_seconds=10,
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(claim, ("worker_1", "worker_2")))
    winners = tuple(item for item in results if item is not None)
    assert len(winners) == 1
    assert WorkflowSchedulingStore(root).get("project_1", "intent_1") == winners[0]


def test_cross_instance_duplicate_create_is_single_revision(tmp_path) -> None:
    root = tmp_path / "scheduling"

    def create(_):
        return WorkflowSchedulingStore(root).create(intent())

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert tuple(pool.map(create, range(2))) == (intent(), intent())
    assert len(WorkflowSchedulingStore(root)._read("project_1")) == 1


def test_reader_waits_for_cross_instance_writer_lock(tmp_path) -> None:
    root = tmp_path / "scheduling"
    writer = WorkflowSchedulingStore(root)
    writer.create(intent())
    reader = WorkflowSchedulingStore(root)
    started = threading.Event()
    completed = threading.Event()

    def read():
        started.set()
        result = reader.list("project_1")
        completed.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        with writer.write_lock("project_1"):
            future = pool.submit(read)
            assert started.wait(timeout=1)
            assert completed.wait(timeout=0.05) is False
        assert future.result(timeout=2) == (intent(),)


def test_capacity_is_bounded_and_terminal_items_release_capacity(tmp_path) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling", capacity=1)
    store.create(intent(1))
    with pytest.raises(OverflowError, match="capacity"):
        store.create(intent(2))
    store.transition("project_1", "intent_1", status=CoordinationStatus.CANCELLED, actor_id="human", timestamp=50, reason="cancelled")
    store.create(intent(2))
    assert len(store.list("project_1")) == 2


def test_corruption_partial_write_and_zero_progress_detection(tmp_path, monkeypatch) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    real_write = os.write

    def short_write(fd, data):
        return real_write(fd, data[:max(1, len(data) // 3)])

    monkeypatch.setattr(os, "write", short_write)
    store.create(intent())
    assert store.get("project_1", "intent_1") is not None
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(OSError, match="no progress"):
        store.create(intent(2))
    monkeypatch.setattr(os, "write", real_write)
    path = store.journal_path("project_1")
    row = json.loads(path.read_text().splitlines()[0])
    row["intent"]["agent_id"] = "tampered"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="corrupt"):
        store.list("project_1")


def test_restart_recovery_removes_only_unterminated_tail(tmp_path) -> None:
    root = tmp_path / "scheduling"
    store = WorkflowSchedulingStore(root)
    first = intent()
    store.create(first)
    path = store.journal_path("project_1")
    with path.open("ab") as handle:
        handle.write(b'{"journal_sequence":2,"torn"')
        handle.flush()
        os.fsync(handle.fileno())
    restarted = WorkflowSchedulingStore(root)
    with pytest.raises(ValueError, match="corrupt"):
        restarted.list("project_1")
    assert restarted.recover_interrupted_tail("project_1") is True
    assert restarted.list("project_1") == (first,)
    assert restarted.recover_interrupted_tail("project_1") is False


def test_recovery_never_hides_terminated_corruption(tmp_path) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    store.create(intent())
    with store.journal_path("project_1").open("ab") as handle:
        handle.write(b"not-json\n")
    with pytest.raises(ValueError, match="corrupt"):
        store.recover_interrupted_tail("project_1")


def test_revision_chain_fork_is_rejected_even_with_valid_checksum(tmp_path) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    store.create(intent())
    claimed = store.claim(
        "project_1", "intent_1", claimed_by="worker",
        timestamp=50, lease_seconds=10,
    )
    path = store.journal_path("project_1")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]["previous_fingerprint"] = "0" * 64
    rows[1]["checksum"] = WorkflowSchedulingJournalRecord.calculate_checksum(
        2, "project_1", "intent_1", 2, "0" * 64, claimed
    )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="revision chain"):
        store.list("project_1")


@pytest.mark.parametrize(
    "updates,error",
    [
        ({"agent_id": "forged-agent"}, "immutable identity"),
        ({
            "status": CoordinationStatus.SCHEDULED,
            "claim_id": None,
            "claimed_by": None,
            "lease_expires_at": None,
        }, "illegal.*transition"),
    ],
)
def test_replay_rejects_checksum_valid_semantically_invalid_revision(
    tmp_path, updates, error
) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    store.create(intent())
    store.claim(
        "project_1", "intent_1", claimed_by="worker",
        timestamp=50, lease_seconds=10,
    )
    path = store.journal_path("project_1")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    forged = WorkflowExecutionIntent.model_validate({
        **rows[1]["intent"],
        **updates,
    })
    rows[1]["intent"] = forged.model_dump(mode="json")
    rows[1]["checksum"] = WorkflowSchedulingJournalRecord.calculate_checksum(
        2, "project_1", "intent_1", 2,
        rows[1]["previous_fingerprint"], forged,
    )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match=error):
        store.list("project_1")


def test_partial_write_exception_is_recoverable_without_losing_prefix(
    tmp_path, monkeypatch
) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    first = intent()
    store.create(first)
    real_write = os.write
    calls = 0

    def partial_then_raise(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:10])
        raise OSError("simulated interrupted append")

    monkeypatch.setattr(os, "write", partial_then_raise)
    with pytest.raises(OSError, match="interrupted append"):
        store.create(intent(2))
    monkeypatch.setattr(os, "write", real_write)
    with pytest.raises(ValueError, match="corrupt"):
        store.list("project_1")
    assert store.recover_interrupted_tail("project_1") is True
    assert store.list("project_1") == (first,)


def test_fsync_failure_leaves_complete_idempotent_revision(
    tmp_path, monkeypatch
) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    real_fsync = os.fsync
    monkeypatch.setattr(
        os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync failed"))
    )
    with pytest.raises(OSError, match="fsync failed"):
        store.create(intent())
    monkeypatch.setattr(os, "fsync", real_fsync)
    restarted = WorkflowSchedulingStore(tmp_path / "scheduling")
    assert restarted.get("project_1", "intent_1") == intent()
    assert restarted.create(intent()) == intent()


def test_invalid_projects_and_secret_text_fail_closed(tmp_path) -> None:
    store = WorkflowSchedulingStore(tmp_path / "scheduling")
    for project_id in ("", ".", "..", "../escape", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="project_id"):
            store.list(project_id)
    with pytest.raises(ValidationError, match="secrets"):
        WorkflowExecutionIntent.model_validate({
            **intent().model_dump(mode="python"),
            "reason": "token=do-not-store",
        })
    with pytest.raises(ValidationError, match="bounded length"):
        WorkflowExecutionIntent.model_validate({
            **intent().model_dump(mode="python"),
            "evidence_refs": ("x" * 513,),
        })
