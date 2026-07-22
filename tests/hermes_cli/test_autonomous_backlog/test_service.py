"""Governed autonomous backlog service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.autonomous_backlog import models as m
from hermes_cli.autonomous_backlog.service import (
    AutonomousBacklogService,
)
from hermes_cli.autonomous_backlog.store import (
    AutonomousBacklogStore,
)


def _service(tmp_path: Path) -> AutonomousBacklogService:
    return AutonomousBacklogService(
        AutonomousBacklogStore(root=tmp_path)
    )


def _source() -> m.BacklogSource:
    return m.BacklogSource(
        source_type=m.BacklogSourceType.HUMAN,
        source_refs=("roadmap:step4",),
        captured_at=10,
        captured_by="maintainer",
    )


def _create(
    service: AutonomousBacklogService,
    *,
    item_id: str = "backlog_1",
    project_id: str = "hermes-platform",
    evidence_requirements: list[
        m.EvidenceRequirement
    ] | None = None,
) -> m.BacklogItem:
    return service.create_item(
        item_id=item_id,
        project_id=project_id,
        title="Build autonomous backlog",
        description="Implement governed backlog execution.",
        source=_source(),
        actor="maintainer",
        priority=m.BacklogPriority.HIGH,
        risk_level=m.BacklogRiskLevel.MEDIUM,
        acceptance_criteria=["Focused tests pass"],
        evidence_requirements=evidence_requirements or [],
        created_at=20,
    )


def test_create_item_persists_candidate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    created = _create(service)
    projected = service.store.get_item(
        "hermes-platform",
        "backlog_1",
    )

    assert created.status == m.BacklogStatus.CANDIDATE
    assert created.version == 1
    assert projected == created
    assert service.store.event_count(
        project_id="hermes-platform"
    ) == 1


def test_create_rejects_duplicate_item_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    with pytest.raises(
        ValueError,
        match="already exists",
    ):
        _create(service)


def test_create_is_idempotent_by_key(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    first = service.create_item(
        item_id="backlog_1",
        project_id="hermes-platform",
        title="Build backlog",
        description="First attempt.",
        source=_source(),
        actor="maintainer",
        idempotency_key="create:step4",
        created_at=20,
    )

    second = service.create_item(
        item_id="backlog_2",
        project_id="hermes-platform",
        title="Build backlog again",
        description="Duplicate attempt.",
        source=_source(),
        actor="maintainer",
        idempotency_key="create:step4",
        created_at=30,
    )

    assert second.item_id == first.item_id
    assert service.store.event_count(
        project_id="hermes-platform"
    ) == 1


def test_candidate_can_be_approved(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    approved = service.approve_item(
        "hermes-platform",
        "backlog_1",
        actor="reviewer",
        expected_version=1,
        updated_at=30,
    )

    assert approved.status == m.BacklogStatus.APPROVED
    assert approved.version == 2
    assert approved.updated_at == 30


def test_approval_detects_version_conflict(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    with pytest.raises(
        ValueError,
        match="backlog version conflict",
    ):
        service.approve_item(
            "hermes-platform",
            "backlog_1",
            expected_version=7,
        )


def test_invalid_transition_fails_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    with pytest.raises(
        ValueError,
        match="invalid backlog transition",
    ):
        service.complete_item(
            "hermes-platform",
            "backlog_1",
            evidence_refs=["pytest:passed"],
        )


def test_approved_item_can_be_scheduled(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    service.approve_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )

    scheduled = service.schedule_item(
        "hermes-platform",
        "backlog_1",
        schedule_policy=m.SchedulePolicy(
            mode=m.ScheduleMode.SCHEDULED,
            scheduled_at=100,
        ),
        updated_at=40,
    )

    assert scheduled.status == m.BacklogStatus.SCHEDULED
    assert scheduled.schedule_policy.scheduled_at == 100
    assert scheduled.version == 3


def test_schedule_requires_scheduled_mode(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    service.approve_item(
        "hermes-platform",
        "backlog_1",
    )

    with pytest.raises(
        ValueError,
        match="requires scheduled mode",
    ):
        service.schedule_item(
            "hermes-platform",
            "backlog_1",
            schedule_policy=m.SchedulePolicy(),
        )


def test_approved_item_can_start(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    service.approve_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )

    started = service.start_item(
        "hermes-platform",
        "backlog_1",
        updated_at=40,
    )

    assert started.status == m.BacklogStatus.EXECUTING
    assert started.version == 3


def test_item_can_be_blocked_with_reason(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    blocked = service.block_item(
        "hermes-platform",
        "backlog_1",
        reason="Waiting for policy approval.",
        blocked_by=["approval_1", " approval_1 "],
        updated_at=30,
    )

    assert blocked.status == m.BacklogStatus.BLOCKED
    assert blocked.blocked_reason == (
        "Waiting for policy approval."
    )
    assert blocked.blocked_by == ["approval_1"]


def test_in_progress_item_can_fail(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    service.approve_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )
    service.start_item(
        "hermes-platform",
        "backlog_1",
        updated_at=40,
    )

    failed = service.fail_item(
        "hermes-platform",
        "backlog_1",
        reason="Focused regression failed.",
        updated_at=50,
    )

    assert failed.status == m.BacklogStatus.FAILED
    assert failed.failure_reason == (
        "Focused regression failed."
    )


def test_completion_requires_model_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    _create(
        service,
        evidence_requirements=[
            m.EvidenceRequirement(
                requirement_id="focused-tests",
                description="Focused tests must pass.",
            )
        ],
    )

    service.approve_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )
    service.start_item(
        "hermes-platform",
        "backlog_1",
        updated_at=40,
    )

    with pytest.raises(
        ValueError,
        match="requires evidence_refs",
    ):
        service.complete_item(
            "hermes-platform",
            "backlog_1",
            evidence_refs=[],
            updated_at=50,
        )


def test_in_progress_item_can_complete_with_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    _create(
        service,
        evidence_requirements=[
            m.EvidenceRequirement(
                requirement_id="focused-tests",
                description="Focused tests must pass.",
            )
        ],
    )

    service.approve_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )
    service.start_item(
        "hermes-platform",
        "backlog_1",
        updated_at=40,
    )

    completed = service.complete_item(
        "hermes-platform",
        "backlog_1",
        evidence_refs=["pytest:passed"],
        updated_at=50,
    )

    assert completed.status == m.BacklogStatus.COMPLETED
    assert completed.evidence_refs == ["pytest:passed"]
    assert completed.version == 4


def test_terminal_item_cannot_transition(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    service.cancel_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )

    with pytest.raises(
        ValueError,
        match="terminal backlog item",
    ):
        service.approve_item(
            "hermes-platform",
            "backlog_1",
        )


def test_supersede_requires_existing_replacement(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    with pytest.raises(
        ValueError,
        match="replacement backlog item",
    ):
        service.supersede_item(
            "hermes-platform",
            "backlog_1",
            superseded_by="backlog_missing",
        )


def test_item_can_be_superseded(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    _create(
        service,
        item_id="backlog_old",
    )
    _create(
        service,
        item_id="backlog_new",
    )

    superseded = service.supersede_item(
        "hermes-platform",
        "backlog_old",
        superseded_by="backlog_new",
        updated_at=30,
    )

    assert superseded.status == m.BacklogStatus.SUPERSEDED
    assert superseded.superseded_by == "backlog_new"


def test_transition_is_idempotent_by_key(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _create(service)

    first = service.approve_item(
        "hermes-platform",
        "backlog_1",
        idempotency_key="approve:1",
        updated_at=30,
    )

    second = service.approve_item(
        "hermes-platform",
        "backlog_1",
        idempotency_key="approve:1",
        updated_at=40,
    )

    assert second == first
    assert service.store.event_count(
        project_id="hermes-platform"
    ) == 2


def test_missing_item_fails_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        ValueError,
        match="does not exist",
    ):
        service.approve_item(
            "hermes-platform",
            "backlog_missing",
        )
