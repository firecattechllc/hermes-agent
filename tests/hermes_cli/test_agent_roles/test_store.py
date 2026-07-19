"""Append-only Specialized Agent Roles store tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.agent_roles import models as m
from hermes_cli.agent_roles import store as s


def _store(tmp_path: Path) -> s.AgentRoleStore:
    return s.AgentRoleStore(tmp_path / "agent-role-store")


def _role(role_id: str = "custom-planner") -> m.AgentRole:
    return m.AgentRole(
        role_id=role_id,
        name="Custom Planner",
        description="Plans bounded engineering work.",
        capabilities=(
            m.RoleCapability(
                capability_id="planning",
                description="Create bounded plans.",
            ),
        ),
    )


def _assignment(
    *,
    assignment_id: str = "assign_1",
    project_id: str = "hermes-platform",
    version: int = 1,
    status: m.AssignmentStatus = m.AssignmentStatus.PENDING,
    assigned_agent_id: str | None = None,
    updated_at: int = 20,
) -> m.Assignment:
    return m.Assignment(
        assignment_id=assignment_id,
        project_id=project_id,
        role_id="builder",
        status=status,
        assigned_agent_id=assigned_agent_id,
        created_at=10,
        updated_at=updated_at,
        version=version,
    )


def test_empty_project_replays_to_empty_state(
    tmp_path: Path,
) -> None:
    state = _store(tmp_path).replay("hermes-platform")

    assert state.project_id == "hermes-platform"
    assert state.sequence == 0
    assert state.roles == {}
    assert state.assignments == {}
    assert state.handoffs == ()
    assert state.results == ()


@pytest.mark.parametrize(
    "project_id",
    [
        "../escape",
        "nested/project",
        "nested\\project",
        ".",
        "..",
        "",
        " project ",
    ],
)
def test_project_ids_cannot_escape_store_root(
    tmp_path: Path,
    project_id: str,
) -> None:
    with pytest.raises(s.InvalidProjectIdError):
        _store(tmp_path).replay(project_id)


def test_role_registration_replays_deterministically(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    role = _role()

    record = store.append_role(
        "hermes-platform",
        role,
        timestamp=10,
    )

    first = store.replay("hermes-platform")
    second = store.replay("hermes-platform")

    assert record.sequence == 1
    assert first == second
    assert first.sequence == 1
    assert first.roles[role.role_id] == role


def test_duplicate_role_id_is_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    role = _role()

    store.append_role(
        "hermes-platform",
        role,
        timestamp=10,
    )

    with pytest.raises(
        s.DuplicateRecordError,
        match="role_id already registered",
    ):
        store.append_role(
            "hermes-platform",
            role,
            timestamp=20,
        )


def test_assignment_snapshots_replay_latest_version(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    first = _assignment(version=1)
    second = _assignment(
        version=2,
        status=m.AssignmentStatus.ACTIVE,
        assigned_agent_id="agent_builder_1",
        updated_at=30,
    )

    store.append_assignment(first, timestamp=20)
    store.append_assignment(second, timestamp=30)

    state = store.replay("hermes-platform")

    assert state.sequence == 2
    assert state.assignments["assign_1"] == second
    assert state.assignments["assign_1"].version == 2


def test_first_assignment_snapshot_must_be_version_one(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    with pytest.raises(
        s.AssignmentVersionError,
        match="expected version 1, got 2",
    ):
        store.append_assignment(
            _assignment(version=2),
            timestamp=20,
        )


def test_assignment_versions_must_be_contiguous(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_assignment(
        _assignment(version=1),
        timestamp=20,
    )

    with pytest.raises(
        s.AssignmentVersionError,
        match="expected version 2, got 3",
    ):
        store.append_assignment(
            _assignment(
                version=3,
                status=m.AssignmentStatus.ACTIVE,
                assigned_agent_id="agent_builder_1",
                updated_at=30,
            ),
            timestamp=30,
        )


def test_handoff_and_result_preserve_append_order(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    handoff = m.AssignmentHandoff(
        handoff_id="handoff_1",
        assignment_id="assign_1",
        project_id="hermes-platform",
        from_role_id="builder",
        to_role_id="reviewer",
        reason=m.HandoffReason.REVIEW_REQUIRED,
        summary="Implementation is ready for review.",
        timestamp=40,
    )

    result = m.AssignmentResult(
        result_id="result_1",
        assignment_id="assign_1",
        project_id="hermes-platform",
        role_id="reviewer",
        outcome=m.AssignmentOutcome.SUCCEEDED,
        summary="Independent review passed.",
        completed_at=50,
    )

    store.append_handoff(handoff)
    store.append_result(result)

    state = store.replay("hermes-platform")

    assert state.sequence == 2
    assert state.handoffs == (handoff,)
    assert state.results == (result,)


def test_duplicate_handoff_and_result_ids_are_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    handoff = m.AssignmentHandoff(
        handoff_id="handoff_1",
        assignment_id="assign_1",
        project_id="hermes-platform",
        from_role_id="builder",
        to_role_id="reviewer",
        reason=m.HandoffReason.REVIEW_REQUIRED,
        summary="Ready for review.",
        timestamp=40,
    )

    result = m.AssignmentResult(
        result_id="result_1",
        assignment_id="assign_1",
        project_id="hermes-platform",
        role_id="reviewer",
        outcome=m.AssignmentOutcome.SUCCEEDED,
        summary="Review passed.",
        completed_at=50,
    )

    store.append_handoff(handoff)
    store.append_result(result)

    with pytest.raises(s.DuplicateRecordError):
        store.append_handoff(handoff)

    with pytest.raises(s.DuplicateRecordError):
        store.append_result(result)


def test_projects_are_strictly_isolated(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_assignment(
        _assignment(
            assignment_id="assign_a",
            project_id="project-a",
        ),
        timestamp=20,
    )

    store.append_assignment(
        _assignment(
            assignment_id="assign_b",
            project_id="project-b",
        ),
        timestamp=20,
    )

    project_a = store.replay("project-a")
    project_b = store.replay("project-b")

    assert set(project_a.assignments) == {"assign_a"}
    assert set(project_b.assignments) == {"assign_b"}
    assert store.journal_path("project-a") != store.journal_path(
        "project-b"
    )


def test_journal_records_are_contiguous_and_checksummed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_role(
        "hermes-platform",
        _role(),
        timestamp=10,
    )
    store.append_assignment(
        _assignment(),
        timestamp=20,
    )

    lines = store.journal_path(
        "hermes-platform"
    ).read_text(encoding="utf-8").splitlines()

    raw_records = [json.loads(line) for line in lines]

    assert [record["sequence"] for record in raw_records] == [1, 2]
    assert all(len(record["checksum"]) == 64 for record in raw_records)


def test_modified_payload_fails_checksum_verification(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_assignment(
        _assignment(),
        timestamp=20,
    )

    journal = store.journal_path("hermes-platform")
    raw = json.loads(journal.read_text(encoding="utf-8"))
    raw["payload"]["role_id"] = "tampered-role"
    journal.write_text(
        json.dumps(raw, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        s.JournalCorruptionError,
        match="checksum mismatch",
    ):
        store.replay("hermes-platform")


def test_missing_sequence_fails_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_role(
        "hermes-platform",
        _role(),
        timestamp=10,
    )
    store.append_assignment(
        _assignment(),
        timestamp=20,
    )

    journal = store.journal_path("hermes-platform")
    lines = journal.read_text(encoding="utf-8").splitlines()
    journal.write_text(lines[1] + "\n", encoding="utf-8")

    with pytest.raises(
        s.JournalCorruptionError,
        match="expected 1, got 2",
    ):
        store.replay("hermes-platform")


def test_incomplete_final_record_fails_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_role(
        "hermes-platform",
        _role(),
        timestamp=10,
    )

    journal = store.journal_path("hermes-platform")

    with journal.open("ab") as handle:
        handle.write(b'{"incomplete":true}')

    with pytest.raises(
        s.JournalCorruptionError,
        match="incomplete final record",
    ):
        store.replay("hermes-platform")


def test_journal_file_is_owner_read_write_only(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.append_role(
        "hermes-platform",
        _role(),
        timestamp=10,
    )

    permissions = (
        store.journal_path("hermes-platform").stat().st_mode & 0o777
    )

    assert permissions == 0o600
