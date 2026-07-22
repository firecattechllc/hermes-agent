"""Autonomous backlog persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.autonomous_backlog import models as m
from hermes_cli.autonomous_backlog.store import (
    AutonomousBacklogStore,
    event_log_path,
    meta_path,
    project_event_log_path,
)


def _source() -> m.BacklogSource:
    return m.BacklogSource(
        source_type=m.BacklogSourceType.HUMAN,
        source_refs=("roadmap:step4",),
        captured_at=10,
        captured_by="maintainer",
    )


def _item(
    *,
    item_id: str = "backlog_1",
    project_id: str = "hermes-platform",
    status: m.BacklogStatus = m.BacklogStatus.CANDIDATE,
    version: int = 1,
) -> m.BacklogItem:
    return m.BacklogItem(
        item_id=item_id,
        project_id=project_id,
        title="Build autonomous backlog persistence",
        description="Implement append-only project journals.",
        status=status,
        priority=m.BacklogPriority.HIGH,
        risk_level=m.BacklogRiskLevel.MEDIUM,
        source=_source(),
        acceptance_criteria=["Focused tests pass"],
        created_at=20,
        updated_at=20,
        created_by="maintainer",
        version=version,
    )


def _event(
    item: m.BacklogItem,
    *,
    event_id: str = "bevt_1",
    event_type: m.BacklogEventType = m.BacklogEventType.CREATED,
    timestamp: int = 20,
    sequence: int = 1,
    expected_version: int | None = 0,
    resulting_version: int = 1,
    idempotency_key: str | None = None,
) -> m.BacklogEvent:
    return m.BacklogEvent(
        event_id=event_id,
        event_type=event_type,
        project_id=item.project_id,
        item_id=item.item_id,
        actor="maintainer",
        timestamp=timestamp,
        sequence=sequence,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        resulting_version=resulting_version,
        payload={
            "item": item.model_dump(mode="json"),
        },
    )


def test_store_initialises_manifest(tmp_path: Path) -> None:
    AutonomousBacklogStore(root=tmp_path)

    metadata = json.loads(
        meta_path(root=tmp_path).read_text()
    )

    assert metadata["schema_version"] == m.CURRENT_SCHEMA_VERSION
    assert metadata["version"] == 1


def test_existing_unsupported_manifest_fails_closed(
    tmp_path: Path,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)

    meta_path(root=tmp_path).write_text(
        json.dumps(
            {
                "schema_version": 999,
                "version": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="schema version 999 not supported",
    ):
        AutonomousBacklogStore(root=tmp_path)


def test_append_event_writes_global_and_project_journals(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)
    event = store.append_event(_event(_item()))

    assert event.sequence == 1
    assert store.event_count() == 1
    assert store.event_count(
        project_id="hermes-platform"
    ) == 1

    global_data = json.loads(
        event_log_path(root=tmp_path).read_text().strip()
    )

    project_data = json.loads(
        project_event_log_path(
            "hermes-platform",
            root=tmp_path,
        ).read_text().strip()
    )

    assert global_data == project_data
    assert global_data["event_id"] == "bevt_1"


def test_sequence_is_monotonic_per_project(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    first = store.append_event(
        _event(
            _item(item_id="backlog_1"),
            event_id="bevt_1",
        )
    )

    second = store.append_event(
        _event(
            _item(item_id="backlog_2"),
            event_id="bevt_2",
        )
    )

    other = store.append_event(
        _event(
            _item(
                item_id="backlog_3",
                project_id="other",
            ),
            event_id="bevt_3",
        )
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert other.sequence == 1


def test_append_event_once_deduplicates_event_id(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)
    event = _event(_item())

    assert store.append_event_once(event) is not None
    assert store.append_event_once(event) is None

    assert store.event_count(
        project_id="hermes-platform"
    ) == 1


def test_append_event_once_deduplicates_idempotency_key(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    first = _event(
        _item(item_id="backlog_1"),
        event_id="bevt_1",
        idempotency_key="mission-control:event-1",
    )

    duplicate = _event(
        _item(item_id="backlog_2"),
        event_id="bevt_2",
        idempotency_key="mission-control:event-1",
    )

    assert store.append_event_once(first) is not None
    assert store.append_event_once(duplicate) is None


def test_batch_append_assigns_project_sequences(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    events = store.append_events(
        [
            _event(
                _item(item_id="backlog_1"),
                event_id="bevt_1",
            ),
            _event(
                _item(item_id="backlog_2"),
                event_id="bevt_2",
            ),
            _event(
                _item(
                    item_id="backlog_3",
                    project_id="other",
                ),
                event_id="bevt_3",
            ),
        ]
    )

    assert [
        event.sequence
        for event in events
    ] == [1, 2, 1]


def test_project_journal_cross_project_record_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    event = _event(
        _item(project_id="project-b")
    )

    path = project_event_log_path(
        "project-a",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            event.model_dump(mode="json")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="project isolation violation",
    ):
        list(
            store.iter_events(
                project_id="project-a"
            )
        )


def test_malformed_jsonl_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    path = project_event_log_path(
        "hermes-platform",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"broken":\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="malformed JSONL line 1",
    ):
        list(
            store.iter_events(
                project_id="hermes-platform"
            )
        )


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "../escape",
        "a/b",
        "a\\b",
        "",
        "bad\x00id",
    ],
)
def test_unsafe_project_ids_are_rejected(
    tmp_path: Path,
    unsafe_id: str,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    with pytest.raises(
        ValueError,
        match="unsafe identifier",
    ):
        list(
            store.iter_events(
                project_id=unsafe_id
            )
        )


def test_list_project_ids_is_sorted(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    store.append_event(
        _event(
            _item(project_id="zeta"),
            event_id="bevt_z",
        )
    )

    store.append_event(
        _event(
            _item(
                item_id="backlog_2",
                project_id="alpha",
            ),
            event_id="bevt_a",
        )
    )

    assert store.list_project_ids() == [
        "alpha",
        "zeta",
    ]


def test_snapshot_replays_latest_item_version(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    candidate = _item(
        status=m.BacklogStatus.CANDIDATE,
        version=1,
    )

    approved = _item(
        status=m.BacklogStatus.APPROVED,
        version=2,
    )

    store.append_event(
        _event(
            candidate,
            event_id="bevt_created",
            event_type=m.BacklogEventType.CREATED,
            timestamp=20,
            expected_version=0,
            resulting_version=1,
        )
    )

    store.append_event(
        _event(
            approved,
            event_id="bevt_approved",
            event_type=m.BacklogEventType.APPROVED,
            timestamp=30,
            expected_version=1,
            resulting_version=2,
        )
    )

    snapshot = store.build_snapshot(
        "hermes-platform",
        generated_by="test",
    )

    assert snapshot.version == 2
    assert snapshot.event_count == 2
    assert len(snapshot.items) == 1
    assert snapshot.items[0].status == m.BacklogStatus.APPROVED
    assert snapshot.items[0].version == 2


def test_snapshot_is_deterministic_for_same_journal(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    store.append_event(
        _event(_item())
    )

    first = store.build_snapshot(
        "hermes-platform",
        generated_by="first",
    )

    second = store.build_snapshot(
        "hermes-platform",
        generated_by="second",
    )

    assert first.integrity_hash() == second.integrity_hash()


def test_snapshot_project_isolation(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    store.append_event(
        _event(
            _item(
                item_id="backlog_a",
                project_id="project-a",
            ),
            event_id="bevt_a",
        )
    )

    store.append_event(
        _event(
            _item(
                item_id="backlog_b",
                project_id="project-b",
            ),
            event_id="bevt_b",
        )
    )

    project_a = store.build_snapshot("project-a")
    project_b = store.build_snapshot("project-b")

    assert [
        item.item_id
        for item in project_a.items
    ] == ["backlog_a"]

    assert [
        item.item_id
        for item in project_b.items
    ] == ["backlog_b"]


def test_item_payload_project_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    item = _item(project_id="project-b")
    event = _event(item)

    data = event.model_dump(mode="json")
    data["project_id"] = "project-a"

    path = project_event_log_path(
        "project-a",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="belongs to project-b",
    ):
        store.build_snapshot("project-a")


def test_item_identity_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    event = _event(_item())
    data = event.model_dump(mode="json")
    data["item_id"] = "backlog_different"

    path = project_event_log_path(
        "hermes-platform",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="backlog item identity mismatch",
    ):
        store.build_snapshot("hermes-platform")


def test_event_without_item_payload_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    event = _event(_item())
    data = event.model_dump(mode="json")
    data["payload"] = {}

    path = project_event_log_path(
        "hermes-platform",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="lacks payload.item",
    ):
        store.build_snapshot("hermes-platform")


def test_item_version_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    event = _event(
        _item(version=1),
        resulting_version=1,
    )

    data = event.model_dump(mode="json")
    data["payload"]["item"]["version"] = 2

    path = project_event_log_path(
        "hermes-platform",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="backlog item version mismatch",
    ):
        store.build_snapshot("hermes-platform")


def test_replay_detects_version_conflict(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    first = _event(
        _item(version=1),
        event_id="bevt_1",
        expected_version=0,
        resulting_version=1,
    )

    second = _event(
        _item(
            status=m.BacklogStatus.APPROVED,
            version=2,
        ),
        event_id="bevt_2",
        event_type=m.BacklogEventType.APPROVED,
        timestamp=30,
        expected_version=0,
        resulting_version=1,
    )

    first_data = first.model_dump(mode="json")
    second_data = second.model_dump(mode="json")
    second_data["payload"]["item"]["version"] = 1

    path = project_event_log_path(
        "hermes-platform",
        root=tmp_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(first_data)
        + "\n"
        + json.dumps(second_data)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="backlog version conflict",
    ):
        store.build_snapshot("hermes-platform")


def test_get_item_returns_projected_item(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)
    store.append_event(_event(_item()))

    item = store.get_item(
        "hermes-platform",
        "backlog_1",
    )

    assert item is not None
    assert item.item_id == "backlog_1"


def test_get_item_returns_none_when_missing(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    assert store.get_item(
        "hermes-platform",
        "backlog_missing",
    ) is None


def test_list_items_filters_status_priority_and_risk(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    high = _item(item_id="backlog_high")

    low_data = _item(
        item_id="backlog_low",
    ).model_dump()

    low_data.update(
        {
            "priority": m.BacklogPriority.LOW,
            "risk_level": m.BacklogRiskLevel.LOW,
        }
    )

    low = m.BacklogItem(**low_data)

    store.append_event(
        _event(
            high,
            event_id="bevt_high",
        )
    )

    store.append_event(
        _event(
            low,
            event_id="bevt_low",
        )
    )

    high_results = store.list_items(
        "hermes-platform",
        priority=m.BacklogPriority.HIGH,
    )

    low_risk_results = store.list_items(
        "hermes-platform",
        risk_level=m.BacklogRiskLevel.LOW,
    )

    assert [
        item.item_id
        for item in high_results
    ] == ["backlog_high"]

    assert [
        item.item_id
        for item in low_risk_results
    ] == ["backlog_low"]


def test_list_items_excludes_terminal_by_default(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(root=tmp_path)

    active = _item(item_id="backlog_active")

    completed_data = _item(
        item_id="backlog_completed",
    ).model_dump()

    completed_data.update(
        {
            "status": m.BacklogStatus.COMPLETED,
            "evidence_refs": ["pytest:passed"],
        }
    )

    completed = m.BacklogItem(**completed_data)

    store.append_event(
        _event(
            active,
            event_id="bevt_active",
        )
    )

    store.append_event(
        _event(
            completed,
            event_id="bevt_completed",
        )
    )

    default_results = store.list_items(
        "hermes-platform",
    )

    all_results = store.list_items(
        "hermes-platform",
        include_terminal=True,
    )

    assert [
        item.item_id
        for item in default_results
    ] == ["backlog_active"]

    assert [
        item.item_id
        for item in all_results
    ] == [
        "backlog_active",
        "backlog_completed",
    ]
