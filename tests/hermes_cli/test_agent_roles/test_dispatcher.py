"""Governed backlog-to-agent-role dispatcher tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.agent_roles import models as role_models
from hermes_cli.agent_roles.dispatcher import (
    BacklogItemNotEligibleError,
    DependencyNotSatisfiedError,
    DispatchPersistenceError,
    DispatchResultStatus,
    GovernedDispatcher,
    MatchingRoleNotFoundError,
)
from hermes_cli.agent_roles.service import AgentRoleService
from hermes_cli.agent_roles.store import AgentRoleStore
from hermes_cli.autonomous_backlog import models as backlog_models
from hermes_cli.autonomous_backlog.service import (
    AutonomousBacklogService,
)
from hermes_cli.autonomous_backlog.store import (
    AutonomousBacklogStore,
)


PROJECT_ID = "hermes-platform"


def _services(
    tmp_path: Path,
) -> tuple[
    AutonomousBacklogService,
    AgentRoleService,
    GovernedDispatcher,
]:
    backlog_service = AutonomousBacklogService(
        AutonomousBacklogStore(
            root=tmp_path / "backlog"
        )
    )
    role_service = AgentRoleService(
        AgentRoleStore(tmp_path / "roles")
    )
    role_service.bootstrap_builtin_roles(
        PROJECT_ID,
        timestamp=1,
    )

    return (
        backlog_service,
        role_service,
        GovernedDispatcher(
            backlog_service,
            role_service,
        ),
    )


def _source() -> backlog_models.BacklogSource:
    return backlog_models.BacklogSource(
        source_type=backlog_models.BacklogSourceType.HUMAN,
        source_refs=("step5.7",),
        captured_at=1,
        captured_by="pytest",
    )


def _approved_item(
    service: AutonomousBacklogService,
    *,
    item_id: str = "backlog_dispatch",
    project_id: str = PROJECT_ID,
    required_capabilities: tuple[str, ...] = ("code-change",),
    allowed_paths: tuple[str, ...] = (
        "hermes_cli/agent_roles/dispatcher.py",
    ),
    dependencies: tuple[str, ...] = (),
    blocked_by: tuple[str, ...] = (),
    priority: backlog_models.BacklogPriority = (
        backlog_models.BacklogPriority.HIGH
    ),
    schedule_policy: backlog_models.SchedulePolicy | None = None,
) -> backlog_models.BacklogItem:
    service.create_item(
        project_id=project_id,
        item_id=item_id,
        title="Build governed dispatcher",
        description="Connect backlog work to agent roles.",
        source=_source(),
        actor="pytest",
        priority=priority,
        risk_level=backlog_models.BacklogRiskLevel.MEDIUM,
        required_capabilities=required_capabilities,
        allowed_paths=allowed_paths,
        dependencies=dependencies,
        blocked_by=blocked_by,
        acceptance_criteria=("Focused tests pass",),
        schedule_policy=schedule_policy,
        created_at=10,
    )

    return service.approve_item(
        project_id,
        item_id,
        actor="pytest",
        updated_at=20,
    )


def test_approved_item_dispatches_to_matching_role(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)
    item = _approved_item(backlog)

    assignment = dispatcher.dispatch_item(
        PROJECT_ID,
        item.item_id,
        timestamp=30,
    )

    assert assignment.role_id == "builder"
    assert assignment.backlog_item_id == item.item_id
    assert assignment.status == role_models.AssignmentStatus.PENDING
    assert assignment.required_capabilities == ("code-change",)
    assert assignment.metadata["risk_level"] == "medium"
    assert assignment.metadata["requested_paths"] == [
        "hermes_cli/agent_roles/dispatcher.py",
    ]

    claimed = backlog.store.get_item(
        PROJECT_ID,
        item.item_id,
    )
    assert claimed is not None
    assert claimed.status == backlog_models.BacklogStatus.CLAIMED
    assert len(roles.list_assignments(PROJECT_ID)) == 1


def test_dispatch_is_idempotent_after_claim(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)
    item = _approved_item(backlog)

    first = dispatcher.dispatch_item(
        PROJECT_ID,
        item.item_id,
        timestamp=30,
    )
    second = dispatcher.dispatch_item(
        PROJECT_ID,
        item.item_id,
        timestamp=40,
    )

    assert second.assignment_id == first.assignment_id
    assert len(roles.list_assignments(PROJECT_ID)) == 1
    assert backlog.store.event_count(
        project_id=PROJECT_ID
    ) == 3


def test_scheduled_item_dispatches_when_due(
    tmp_path: Path,
) -> None:
    backlog, _, dispatcher = _services(tmp_path)
    item = _approved_item(backlog)

    backlog.schedule_item(
        PROJECT_ID,
        item.item_id,
        schedule_policy=backlog_models.SchedulePolicy(
            mode=backlog_models.ScheduleMode.SCHEDULED,
            scheduled_at=100,
        ),
        updated_at=30,
    )

    assignment = dispatcher.dispatch_item(
        PROJECT_ID,
        item.item_id,
        timestamp=100,
    )

    assert assignment.role_id == "builder"
    claimed = backlog.store.get_item(
        PROJECT_ID,
        item.item_id,
    )
    assert claimed is not None
    assert claimed.status == backlog_models.BacklogStatus.CLAIMED


def test_scheduled_item_before_due_time_fails_closed(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)
    item = _approved_item(backlog)

    backlog.schedule_item(
        PROJECT_ID,
        item.item_id,
        schedule_policy=backlog_models.SchedulePolicy(
            mode=backlog_models.ScheduleMode.SCHEDULED,
            scheduled_at=100,
        ),
        updated_at=30,
    )

    with pytest.raises(
        BacklogItemNotEligibleError,
        match="scheduled for 100",
    ):
        dispatcher.dispatch_item(
            PROJECT_ID,
            item.item_id,
            timestamp=99,
        )

    assert roles.list_assignments(PROJECT_ID) == ()


def test_incomplete_dependency_refuses_dispatch(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)

    _approved_item(
        backlog,
        item_id="backlog_dependency",
        required_capabilities=(),
        allowed_paths=(),
    )
    item = _approved_item(
        backlog,
        item_id="backlog_dependent",
        dependencies=("backlog_dependency",),
    )

    with pytest.raises(
        DependencyNotSatisfiedError,
        match="not completed",
    ):
        dispatcher.dispatch_item(
            PROJECT_ID,
            item.item_id,
            timestamp=30,
        )

    assert roles.list_assignments(PROJECT_ID) == ()


def test_completed_dependency_allows_dispatch(
    tmp_path: Path,
) -> None:
    backlog, _, dispatcher = _services(tmp_path)

    dependency = _approved_item(
        backlog,
        item_id="backlog_dependency",
        required_capabilities=(),
        allowed_paths=(),
    )
    backlog.start_item(
        PROJECT_ID,
        dependency.item_id,
        updated_at=30,
    )
    backlog.complete_item(
        PROJECT_ID,
        dependency.item_id,
        evidence_refs=("pytest:passed",),
        updated_at=40,
    )

    item = _approved_item(
        backlog,
        item_id="backlog_dependent",
        dependencies=(dependency.item_id,),
    )

    assignment = dispatcher.dispatch_item(
        PROJECT_ID,
        item.item_id,
        timestamp=50,
    )

    assert assignment.backlog_item_id == item.item_id


def test_no_matching_capability_fails_closed(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)
    item = _approved_item(
        backlog,
        required_capabilities=("quantum-compiler",),
        allowed_paths=(),
    )

    with pytest.raises(
        MatchingRoleNotFoundError,
        match="no active",
    ):
        dispatcher.dispatch_item(
            PROJECT_ID,
            item.item_id,
            timestamp=30,
        )

    assert roles.list_assignments(PROJECT_ID) == ()


def test_explicit_role_must_match_item_policy(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)
    item = _approved_item(backlog)

    with pytest.raises(
        MatchingRoleNotFoundError,
        match="requested role 'reviewer'",
    ):
        dispatcher.dispatch_item(
            PROJECT_ID,
            item.item_id,
            timestamp=30,
            role_id="reviewer",
        )

    assert roles.list_assignments(PROJECT_ID) == ()


def test_claim_failure_cancels_new_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)
    item = _approved_item(backlog)

    def _fail_transition(*args: object, **kwargs: object) -> object:
        raise ValueError("simulated claim failure")

    monkeypatch.setattr(
        backlog,
        "transition_item",
        _fail_transition,
    )

    with pytest.raises(
        DispatchPersistenceError,
        match="could not claim",
    ):
        dispatcher.dispatch_item(
            PROJECT_ID,
            item.item_id,
            timestamp=30,
        )

    assignments = roles.list_assignments(PROJECT_ID)
    assert len(assignments) == 1
    assert assignments[0].status == (
        role_models.AssignmentStatus.CANCELLED
    )

    results = roles.list_results(PROJECT_ID)
    assert len(results) == 1
    assert results[0].metadata["dispatch_compensation"] is True

    unchanged = backlog.store.get_item(
        PROJECT_ID,
        item.item_id,
    )
    assert unchanged is not None
    assert unchanged.status == backlog_models.BacklogStatus.APPROVED


def test_dispatch_is_project_isolated(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)

    roles.bootstrap_builtin_roles(
        "project-b",
        timestamp=1,
    )
    item = _approved_item(
        backlog,
        project_id="project-b",
        item_id="backlog_project_b",
    )

    assignment = dispatcher.dispatch_item(
        "project-b",
        item.item_id,
        timestamp=30,
    )

    assert assignment.project_id == "project-b"
    assert roles.list_assignments(PROJECT_ID) == ()
    assert len(roles.list_assignments("project-b")) == 1



def test_ready_dispatch_uses_deterministic_priority_order(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)

    _approved_item(
        backlog,
        item_id="backlog_low",
        priority=backlog_models.BacklogPriority.LOW,
    )
    _approved_item(
        backlog,
        item_id="backlog_critical",
        priority=backlog_models.BacklogPriority.CRITICAL,
    )
    _approved_item(
        backlog,
        item_id="backlog_high",
        priority=backlog_models.BacklogPriority.HIGH,
    )

    report = dispatcher.dispatch_ready_items(
        PROJECT_ID,
        timestamp=30,
        limit=2,
    )

    assert tuple(
        result.item_id
        for result in report.results
    ) == (
        "backlog_critical",
        "backlog_high",
    )
    assert report.claimed_count == 2
    assert report.blocked_count == 0
    assert report.skipped_count == 0
    assert len(roles.list_assignments(PROJECT_ID)) == 2

    low = backlog.store.get_item(
        PROJECT_ID,
        "backlog_low",
    )
    assert low is not None
    assert low.status == backlog_models.BacklogStatus.APPROVED


def test_ready_dispatch_reports_not_due_scheduled_item(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)

    item = _approved_item(
        backlog,
        item_id="backlog_future",
    )
    backlog.schedule_item(
        PROJECT_ID,
        item.item_id,
        schedule_policy=backlog_models.SchedulePolicy(
            mode=backlog_models.ScheduleMode.SCHEDULED,
            scheduled_at=100,
        ),
        updated_at=25,
    )

    report = dispatcher.dispatch_ready_items(
        PROJECT_ID,
        timestamp=99,
    )

    assert len(report.results) == 1
    assert report.results[0].item_id == item.item_id
    assert report.results[0].status == (
        DispatchResultStatus.SKIPPED
    )
    assert "scheduled for 100" in (
        report.results[0].reason or ""
    )
    assert report.skipped_count == 1
    assert roles.list_assignments(PROJECT_ID) == ()


def test_ready_dispatch_respects_role_concurrency_limit(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)

    for item_id in (
        "backlog_builder_a",
        "backlog_builder_b",
        "backlog_builder_c",
    ):
        _approved_item(
            backlog,
            item_id=item_id,
        )

    report = dispatcher.dispatch_ready_items(
        PROJECT_ID,
        timestamp=30,
    )

    assert report.claimed_count == 2
    assert report.skipped_count == 1
    assert tuple(
        result.status
        for result in report.results
    ) == (
        DispatchResultStatus.CLAIMED,
        DispatchResultStatus.CLAIMED,
        DispatchResultStatus.SKIPPED,
    )
    assert len(roles.list_assignments(PROJECT_ID)) == 2

    remaining = backlog.store.get_item(
        PROJECT_ID,
        "backlog_builder_c",
    )
    assert remaining is not None
    assert remaining.status == backlog_models.BacklogStatus.APPROVED


def test_ready_dispatch_reports_blocked_dependencies(
    tmp_path: Path,
) -> None:
    backlog, roles, dispatcher = _services(tmp_path)

    _approved_item(
        backlog,
        item_id="backlog_dependency",
        required_capabilities=(),
        allowed_paths=(),
    )
    _approved_item(
        backlog,
        item_id="backlog_waiting",
        dependencies=("backlog_dependency",),
    )

    report = dispatcher.dispatch_ready_items(
        PROJECT_ID,
        timestamp=30,
        limit=1,
    )

    assert len(report.results) == 1
    assert report.results[0].item_id == "backlog_dependency"
    assert report.results[0].status == DispatchResultStatus.CLAIMED
    assert len(roles.list_assignments(PROJECT_ID)) == 1


def test_ready_dispatch_rejects_negative_limit(
    tmp_path: Path,
) -> None:
    _, _, dispatcher = _services(tmp_path)

    with pytest.raises(
        ValueError,
        match="limit must be non-negative",
    ):
        dispatcher.dispatch_ready_items(
            PROJECT_ID,
            timestamp=30,
            limit=-1,
        )
