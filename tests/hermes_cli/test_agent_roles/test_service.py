"""Governed Specialized Agent Roles service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.agent_roles import models as m
from hermes_cli.agent_roles import service as svc
from hermes_cli.agent_roles import store as s


def _service(tmp_path: Path) -> svc.AgentRoleService:
    store = s.AgentRoleStore(tmp_path / "agent-role-store")
    return svc.AgentRoleService(store)


def _custom_role(
    *,
    role_id: str = "architecture",
    active: bool = True,
    built_in: bool = False,
    capabilities: tuple[m.RoleCapability, ...] | None = None,
) -> m.AgentRole:
    if capabilities is None:
        capabilities = (
            m.RoleCapability(
                capability_id="architecture-review",
                description="Review system architecture.",
            ),
        )

    return m.AgentRole(
        role_id=role_id,
        name="Architecture",
        description="Reviews architecture and system boundaries.",
        capabilities=capabilities,
        built_in=built_in,
        active=active,
    )


def test_empty_project_has_no_roles(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service.list_roles("hermes-platform") == ()


def test_missing_role_fails_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        svc.RoleNotFoundError,
        match="role is not registered",
    ):
        service.get_role(
            "hermes-platform",
            "builder",
        )


def test_find_role_returns_none_when_missing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service.find_role(
        "hermes-platform",
        "builder",
    ) is None


def test_bootstrap_registers_all_builtin_roles(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    roles = service.bootstrap_builtin_roles(
        "hermes-platform",
        timestamp=10,
    )

    assert len(roles) == 7
    assert {role.role_id for role in roles} == {
        member.value
        for member in m.BuiltinRole
    }
    assert all(role.built_in for role in roles)


def test_bootstrap_is_idempotent(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    first = service.bootstrap_builtin_roles(
        "hermes-platform",
        timestamp=10,
    )
    first_sequence = service.get_project_state(
        "hermes-platform"
    ).sequence

    second = service.bootstrap_builtin_roles(
        "hermes-platform",
        timestamp=20,
    )
    second_sequence = service.get_project_state(
        "hermes-platform"
    ).sequence

    assert first == second
    assert first_sequence == 7
    assert second_sequence == 7


def test_bootstrap_repairs_partial_catalog(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    planner = next(
        role
        for role in m.builtin_agent_roles()
        if role.role_id == m.BuiltinRole.PLANNER.value
    )

    service.store.append_role(
        "hermes-platform",
        planner,
        timestamp=10,
    )

    roles = service.bootstrap_builtin_roles(
        "hermes-platform",
        timestamp=20,
    )

    assert len(roles) == 7
    assert service.get_project_state(
        "hermes-platform"
    ).sequence == 7


def test_bootstrap_rejects_conflicting_builtin_definition(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    conflicting_builder = m.AgentRole(
        role_id="builder",
        name="Conflicting Builder",
        description="This must not replace the governed catalog.",
        capabilities=(
            m.RoleCapability(
                capability_id="code-change",
                description="Modify implementation files.",
            ),
        ),
        built_in=False,
    )

    service.store.append_role(
        "hermes-platform",
        conflicting_builder,
        timestamp=10,
    )

    with pytest.raises(
        svc.BuiltinRoleConflictError,
        match="stored built-in role differs",
    ):
        service.bootstrap_builtin_roles(
            "hermes-platform",
            timestamp=20,
        )


def test_custom_role_registration_round_trips(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    role = _custom_role()

    registered = service.register_custom_role(
        "hermes-platform",
        role,
        timestamp=10,
    )

    assert registered == role
    assert service.get_role(
        "hermes-platform",
        role.role_id,
    ) == role


def test_custom_role_registration_is_project_isolated(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    role = _custom_role()

    service.register_custom_role(
        "project-a",
        role,
        timestamp=10,
    )

    assert service.get_role(
        "project-a",
        role.role_id,
    ) == role
    assert service.find_role(
        "project-b",
        role.role_id,
    ) is None


def test_duplicate_custom_role_is_rejected(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    role = _custom_role()

    service.register_custom_role(
        "hermes-platform",
        role,
        timestamp=10,
    )

    with pytest.raises(
        svc.RoleAlreadyRegisteredError,
        match="role_id already registered",
    ):
        service.register_custom_role(
            "hermes-platform",
            role,
            timestamp=20,
        )


def test_custom_role_cannot_claim_builtin_flag(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        svc.InvalidRoleRegistrationError,
        match="must not set built_in=True",
    ):
        service.register_custom_role(
            "hermes-platform",
            _custom_role(built_in=True),
            timestamp=10,
        )


@pytest.mark.parametrize(
    "role_id",
    [member.value for member in m.BuiltinRole],
)
def test_custom_role_cannot_reuse_builtin_identifier(
    tmp_path: Path,
    role_id: str,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        svc.InvalidRoleRegistrationError,
        match="must not reuse a built-in role_id",
    ):
        service.register_custom_role(
            "hermes-platform",
            _custom_role(role_id=role_id),
            timestamp=10,
        )


def test_new_custom_role_must_be_active(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        svc.InvalidRoleRegistrationError,
        match="must be active",
    ):
        service.register_custom_role(
            "hermes-platform",
            _custom_role(active=False),
            timestamp=10,
        )


def test_custom_role_requires_capability(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(
        svc.InvalidRoleRegistrationError,
        match="at least one capability",
    ):
        service.register_custom_role(
            "hermes-platform",
            _custom_role(capabilities=()),
            timestamp=10,
        )


def test_custom_role_requires_one_required_capability(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    role = _custom_role(
        capabilities=(
            m.RoleCapability(
                capability_id="optional-analysis",
                description="Optional analysis.",
                required=False,
            ),
        ),
    )

    with pytest.raises(
        svc.InvalidRoleRegistrationError,
        match="at least one required capability",
    ):
        service.register_custom_role(
            "hermes-platform",
            role,
            timestamp=10,
        )


def test_duplicate_capability_ids_are_rejected_by_model(
    tmp_path: Path,
) -> None:
    _ = tmp_path

    duplicate_capabilities = (
        m.RoleCapability(
            capability_id="architecture-review",
            description="Review architecture.",
        ),
        m.RoleCapability(
            capability_id="architecture-review",
            description="Review architecture independently.",
        ),
    )

    with pytest.raises(
        ValueError,
        match="unique capability IDs",
    ):
        m.AgentRole(
            role_id="architecture",
            name="Architecture",
            description="Reviews architecture and system boundaries.",
            capabilities=duplicate_capabilities,
        )


def test_list_roles_is_sorted_by_role_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.register_custom_role(
        "hermes-platform",
        _custom_role(role_id="zeta-role"),
        timestamp=10,
    )
    service.register_custom_role(
        "hermes-platform",
        _custom_role(role_id="alpha-role"),
        timestamp=20,
    )

    role_ids = tuple(
        role.role_id
        for role in service.list_roles("hermes-platform")
    )

    assert role_ids == (
        "alpha-role",
        "zeta-role",
    )


def test_list_roles_can_filter_inactive_records(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    active = _custom_role(role_id="active-role")
    inactive = m.AgentRole(
        role_id="inactive-role",
        name="Inactive",
        description="Historical inactive role.",
        capabilities=(
            m.RoleCapability(
                capability_id="historical-analysis",
                description="Analyze historical decisions.",
            ),
        ),
        active=False,
    )

    service.store.append_role(
        "hermes-platform",
        active,
        timestamp=10,
    )
    service.store.append_role(
        "hermes-platform",
        inactive,
        timestamp=20,
    )

    all_roles = service.list_roles("hermes-platform")
    active_roles = service.list_roles(
        "hermes-platform",
        active_only=True,
    )

    assert {role.role_id for role in all_roles} == {
        "active-role",
        "inactive-role",
    }
    assert tuple(
        role.role_id
        for role in active_roles
    ) == ("active-role",)


def test_capability_helpers_preserve_model_order(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    role = _custom_role(
        capabilities=(
            m.RoleCapability(
                capability_id="first",
                description="First capability.",
            ),
            m.RoleCapability(
                capability_id="second",
                description="Second capability.",
                required=False,
            ),
            m.RoleCapability(
                capability_id="third",
                description="Third capability.",
            ),
        ),
    )

    assert service.role_capability_ids(role) == (
        "first",
        "second",
        "third",
    )
    assert service.role_has_capability(role, "second")
    assert not service.role_has_capability(role, "missing")
    assert tuple(
        capability.capability_id
        for capability in service.required_capabilities(role)
    ) == (
        "first",
        "third",
    )


def _bootstrapped_service(
    tmp_path: Path,
) -> svc.AgentRoleService:
    service = _service(tmp_path)
    service.bootstrap_builtin_roles(
        "hermes-platform",
        timestamp=1,
    )
    return service


def test_create_assignment_for_registered_role(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)

    assignment = service.create_assignment(
        "hermes-platform",
        "builder",
        assignment_id="assign_test_create",
        timestamp=10,
        required_capabilities=("code-change",),
        backlog_item_id="backlog-1",
        instructions="Implement the bounded change.",
        created_by="operator",
    )

    assert assignment.assignment_id == "assign_test_create"
    assert assignment.project_id == "hermes-platform"
    assert assignment.role_id == "builder"
    assert assignment.status == m.AssignmentStatus.PENDING
    assert assignment.assigned_agent_id is None
    assert assignment.version == 1
    assert assignment.created_at == 10
    assert assignment.updated_at == 10


def test_create_assignment_requires_registered_role(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(svc.RoleNotFoundError):
        service.create_assignment(
            "hermes-platform",
            "builder",
            timestamp=10,
        )


def test_create_assignment_rejects_missing_capability(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)

    with pytest.raises(
        svc.InvalidAssignmentError,
        match="does not provide required capabilities",
    ):
        service.create_assignment(
            "hermes-platform",
            "builder",
            timestamp=10,
            required_capabilities=("security-review",),
        )


def test_create_assignment_rejects_inactive_role(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    inactive = m.AgentRole(
        role_id="inactive-role",
        name="Inactive",
        description="Inactive role.",
        capabilities=(
            m.RoleCapability(
                capability_id="analysis",
                description="Perform analysis.",
            ),
        ),
        active=False,
    )

    service.store.append_role(
        "hermes-platform",
        inactive,
        timestamp=1,
    )

    with pytest.raises(
        svc.InvalidAssignmentError,
        match="role is inactive",
    ):
        service.create_assignment(
            "hermes-platform",
            "inactive-role",
            timestamp=10,
        )


def test_create_assignment_rejects_duplicate_identifier(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)

    service.create_assignment(
        "hermes-platform",
        "builder",
        assignment_id="assign_duplicate",
        timestamp=10,
    )

    with pytest.raises(
        svc.InvalidAssignmentError,
        match="assignment_id already exists",
    ):
        service.create_assignment(
            "hermes-platform",
            "builder",
            assignment_id="assign_duplicate",
            timestamp=20,
        )


def test_assignment_lookup_fails_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service.find_assignment(
        "hermes-platform",
        "missing",
    ) is None

    with pytest.raises(
        svc.AssignmentNotFoundError,
        match="assignment is not registered",
    ):
        service.get_assignment(
            "hermes-platform",
            "missing",
        )


def test_assign_agent_moves_pending_to_assigned(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        assignment_id="assign_lifecycle",
        timestamp=10,
    )

    assigned = service.assign_agent(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )

    assert assigned.status == m.AssignmentStatus.ASSIGNED
    assert assigned.assigned_agent_id == "agent-builder-1"
    assert assigned.version == 2
    assert assigned.updated_at == 20


def test_accept_assignment_moves_assigned_to_accepted(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=10,
    )
    assigned = service.assign_agent(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )

    accepted = service.accept_assignment(
        "hermes-platform",
        assigned.assignment_id,
        agent_id="agent-builder-1",
        timestamp=30,
    )

    assert accepted.status == m.AssignmentStatus.ACCEPTED
    assert accepted.version == 3
    assert accepted.assigned_agent_id == "agent-builder-1"


def test_activate_assignment_moves_accepted_to_active(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=10,
    )
    assigned = service.assign_agent(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )
    accepted = service.accept_assignment(
        "hermes-platform",
        assigned.assignment_id,
        agent_id="agent-builder-1",
        timestamp=30,
    )

    active = service.activate_assignment(
        "hermes-platform",
        accepted.assignment_id,
        agent_id="agent-builder-1",
        timestamp=40,
    )

    assert active.status == m.AssignmentStatus.ACTIVE
    assert active.version == 4
    assert active.updated_at == 40


def test_assignment_lifecycle_rejects_wrong_order(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=10,
    )

    with pytest.raises(
        svc.InvalidAssignmentTransitionError,
        match="cannot activate assignment",
    ):
        service.activate_assignment(
            "hermes-platform",
            created.assignment_id,
            agent_id="agent-builder-1",
            timestamp=20,
        )


def test_assignment_lifecycle_rejects_wrong_agent(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=10,
    )
    assigned = service.assign_agent(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )

    with pytest.raises(
        svc.AssignmentAgentMismatchError,
        match="assignment belongs to agent",
    ):
        service.accept_assignment(
            "hermes-platform",
            assigned.assignment_id,
            agent_id="agent-builder-2",
            timestamp=30,
        )


def test_assignment_lifecycle_rejects_backward_timestamp(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=20,
    )

    with pytest.raises(
        svc.InvalidAssignmentTransitionError,
        match="timestamp must not move backwards",
    ):
        service.assign_agent(
            "hermes-platform",
            created.assignment_id,
            agent_id="agent-builder-1",
            timestamp=19,
        )


def test_assignment_versions_replay_deterministically(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        assignment_id="assign_replay",
        timestamp=10,
    )
    service.assign_agent(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )
    service.accept_assignment(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=30,
    )
    service.activate_assignment(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=40,
    )

    replayed = service.store.replay(
        "hermes-platform"
    ).get_assignment("assign_replay")

    assert replayed is not None
    assert replayed.status == m.AssignmentStatus.ACTIVE
    assert replayed.version == 4


def test_assignments_are_project_isolated(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.bootstrap_builtin_roles("project-a", timestamp=1)
    service.bootstrap_builtin_roles("project-b", timestamp=1)

    assignment = service.create_assignment(
        "project-a",
        "builder",
        assignment_id="assign_isolated",
        timestamp=10,
    )

    assert service.get_assignment(
        "project-a",
        assignment.assignment_id,
    ) == assignment
    assert service.find_assignment(
        "project-b",
        assignment.assignment_id,
    ) is None


def test_list_assignments_filters_role_and_status(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)

    builder = service.create_assignment(
        "hermes-platform",
        "builder",
        assignment_id="assign_builder",
        timestamp=10,
    )
    service.create_assignment(
        "hermes-platform",
        "reviewer",
        assignment_id="assign_reviewer",
        timestamp=11,
    )
    service.assign_agent(
        "hermes-platform",
        builder.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )

    assigned = service.list_assignments(
        "hermes-platform",
        status=m.AssignmentStatus.ASSIGNED,
    )
    reviewers = service.list_assignments(
        "hermes-platform",
        role_id="reviewer",
    )

    assert tuple(
        item.assignment_id for item in assigned
    ) == ("assign_builder",)
    assert tuple(
        item.assignment_id for item in reviewers
    ) == ("assign_reviewer",)


def _active_assignment(
    service: svc.AgentRoleService,
    *,
    assignment_id: str = "assign_active",
) -> m.Assignment:
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        assignment_id=assignment_id,
        timestamp=10,
    )
    service.assign_agent(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )
    service.accept_assignment(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=30,
    )
    return service.activate_assignment(
        "hermes-platform",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=40,
    )


def test_block_and_unblock_assignment(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    blocked = service.block_assignment(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        timestamp=50,
    )
    unblocked = service.unblock_assignment(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        timestamp=60,
    )

    assert blocked.status == m.AssignmentStatus.BLOCKED
    assert blocked.version == 5
    assert unblocked.status == m.AssignmentStatus.ACTIVE
    assert unblocked.version == 6


def test_block_assignment_requires_active_state(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    created = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=10,
    )

    with pytest.raises(
        svc.InvalidAssignmentTransitionError,
        match="cannot block assignment",
    ):
        service.block_assignment(
            "hermes-platform",
            created.assignment_id,
            agent_id="agent-builder-1",
            timestamp=20,
        )


def test_block_assignment_enforces_agent_ownership(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    with pytest.raises(svc.AssignmentAgentMismatchError):
        service.block_assignment(
            "hermes-platform",
            active.assignment_id,
            agent_id="agent-builder-2",
            timestamp=50,
        )


def test_request_handoff_records_evidence_and_state(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    updated, handoff = service.request_handoff(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        to_role_id="reviewer",
        reason=m.HandoffReason.REVIEW_REQUIRED,
        summary="Implementation is ready for review.",
        timestamp=50,
        evidence_refs=("commit:abc123", "tests:85-passed"),
        handoff_id="handoff_service_test",
    )

    assert updated.status == m.AssignmentStatus.HANDOFF_REQUESTED
    assert updated.version == 5
    assert handoff.from_role_id == "builder"
    assert handoff.to_role_id == "reviewer"
    assert handoff.requested_by == "agent-builder-1"
    assert handoff.evidence_refs == (
        "commit:abc123",
        "tests:85-passed",
    )
    assert service.list_handoffs(
        "hermes-platform",
        assignment_id=active.assignment_id,
    ) == (handoff,)


def test_request_handoff_requires_active_target_role(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    inactive = m.AgentRole(
        role_id="inactive-target",
        name="Inactive target",
        description="Inactive handoff target.",
        capabilities=(
            m.RoleCapability(
                capability_id="review",
                description="Review work.",
            ),
        ),
        active=False,
    )
    service.store.append_role(
        "hermes-platform",
        inactive,
        timestamp=45,
    )

    with pytest.raises(
        svc.InvalidAssignmentError,
        match="handoff target role is inactive",
    ):
        service.request_handoff(
            "hermes-platform",
            active.assignment_id,
            agent_id="agent-builder-1",
            to_role_id="inactive-target",
            reason=m.HandoffReason.REVIEW_REQUIRED,
            summary="Review required.",
            timestamp=50,
        )


def test_request_handoff_rejects_duplicate_identifier(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    first = _active_assignment(
        service,
        assignment_id="assign_handoff_one",
    )
    second = _active_assignment(
        service,
        assignment_id="assign_handoff_two",
    )

    service.request_handoff(
        "hermes-platform",
        first.assignment_id,
        agent_id="agent-builder-1",
        to_role_id="reviewer",
        reason=m.HandoffReason.REVIEW_REQUIRED,
        summary="First request.",
        timestamp=50,
        handoff_id="handoff_duplicate",
    )

    with pytest.raises(
        svc.InvalidAssignmentError,
        match="handoff_id already exists",
    ):
        service.request_handoff(
            "hermes-platform",
            second.assignment_id,
            agent_id="agent-builder-1",
            to_role_id="reviewer",
            reason=m.HandoffReason.REVIEW_REQUIRED,
            summary="Second request.",
            timestamp=50,
            handoff_id="handoff_duplicate",
        )


def test_complete_handoff_moves_to_handed_off(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    requested, _ = service.request_handoff(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        to_role_id="reviewer",
        reason=m.HandoffReason.REVIEW_REQUIRED,
        summary="Ready for review.",
        timestamp=50,
    )

    handed_off = service.complete_handoff(
        "hermes-platform",
        requested.assignment_id,
        agent_id="agent-builder-1",
        timestamp=60,
    )

    assert handed_off.status == m.AssignmentStatus.HANDED_OFF
    assert handed_off.version == 6


def test_complete_handoff_requires_recorded_request(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    payload = active.model_dump(mode="python")
    payload.update(
        {
            "status": m.AssignmentStatus.HANDOFF_REQUESTED,
            "updated_at": 50,
            "version": 5,
        }
    )
    service.store.append_assignment(
        m.Assignment.model_validate(payload),
        timestamp=50,
    )

    with pytest.raises(
        svc.InvalidAssignmentTransitionError,
        match="without a recorded request",
    ):
        service.complete_handoff(
            "hermes-platform",
            active.assignment_id,
            agent_id="agent-builder-1",
            timestamp=60,
        )


def test_complete_assignment_records_success_result(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    completed, result = service.complete_assignment(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        summary="Implementation and focused verification completed.",
        timestamp=50,
        evidence_refs=("commit:abc123", "pytest:passed"),
        result_id="result_success",
    )

    assert completed.status == m.AssignmentStatus.COMPLETED
    assert completed.version == 5
    assert result.outcome == m.AssignmentOutcome.SUCCEEDED
    assert result.role_id == "builder"
    assert result.produced_by == "agent-builder-1"
    assert service.list_results(
        "hermes-platform",
        assignment_id=active.assignment_id,
    ) == (result,)


def test_fail_assignment_from_active(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    failed, result = service.fail_assignment(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        summary="Focused verification failed.",
        timestamp=50,
        evidence_refs=("pytest:failed",),
    )

    assert failed.status == m.AssignmentStatus.FAILED
    assert result.outcome == m.AssignmentOutcome.FAILED


def test_fail_assignment_from_blocked(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)
    blocked = service.block_assignment(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        timestamp=50,
    )

    failed, result = service.fail_assignment(
        "hermes-platform",
        blocked.assignment_id,
        agent_id="agent-builder-1",
        summary="Blocker could not be resolved.",
        timestamp=60,
    )

    assert failed.status == m.AssignmentStatus.FAILED
    assert failed.version == 6
    assert result.outcome == m.AssignmentOutcome.FAILED


def test_cancel_pending_assignment_without_agent(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    pending = service.create_assignment(
        "hermes-platform",
        "builder",
        timestamp=10,
    )

    cancelled, result = service.cancel_assignment(
        "hermes-platform",
        pending.assignment_id,
        summary="Operator withdrew the assignment.",
        timestamp=20,
    )

    assert cancelled.status == m.AssignmentStatus.CANCELLED
    assert result.outcome == m.AssignmentOutcome.CANCELLED
    assert result.produced_by is None


def test_cancel_assigned_work_requires_owner(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    with pytest.raises(
        svc.AssignmentAgentMismatchError,
        match="requires agent_id",
    ):
        service.cancel_assignment(
            "hermes-platform",
            active.assignment_id,
            summary="Cancel active assignment.",
            timestamp=50,
        )


def test_terminal_assignment_cannot_complete_twice(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    completed, _ = service.complete_assignment(
        "hermes-platform",
        active.assignment_id,
        agent_id="agent-builder-1",
        summary="Completed.",
        timestamp=50,
    )

    with pytest.raises(
        svc.InvalidAssignmentTransitionError,
        match="cannot move assignment from completed",
    ):
        service.complete_assignment(
            "hermes-platform",
            completed.assignment_id,
            agent_id="agent-builder-1",
            summary="Completed again.",
            timestamp=60,
        )


def test_terminal_result_rejects_duplicate_identifier(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    first = _active_assignment(
        service,
        assignment_id="assign_result_one",
    )
    second = _active_assignment(
        service,
        assignment_id="assign_result_two",
    )

    service.complete_assignment(
        "hermes-platform",
        first.assignment_id,
        agent_id="agent-builder-1",
        summary="First completed assignment.",
        timestamp=50,
        result_id="result_duplicate",
    )

    with pytest.raises(
        svc.InvalidAssignmentError,
        match="result_id already exists",
    ):
        service.complete_assignment(
            "hermes-platform",
            second.assignment_id,
            agent_id="agent-builder-1",
            summary="Second completed assignment.",
            timestamp=50,
            result_id="result_duplicate",
        )

    still_active = service.get_assignment(
        "hermes-platform",
        second.assignment_id,
    )
    assert still_active.status == m.AssignmentStatus.ACTIVE


def test_terminal_transition_rejects_backward_timestamp(
    tmp_path: Path,
) -> None:
    service = _bootstrapped_service(tmp_path)
    active = _active_assignment(service)

    with pytest.raises(
        svc.InvalidAssignmentTransitionError,
        match="result timestamp must not move backwards",
    ):
        service.complete_assignment(
            "hermes-platform",
            active.assignment_id,
            agent_id="agent-builder-1",
            summary="Invalid old result.",
            timestamp=39,
        )


def test_handoffs_and_results_are_project_isolated(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.bootstrap_builtin_roles("project-a", timestamp=1)
    service.bootstrap_builtin_roles("project-b", timestamp=1)

    created = service.create_assignment(
        "project-a",
        "builder",
        timestamp=10,
    )
    service.assign_agent(
        "project-a",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=20,
    )
    service.accept_assignment(
        "project-a",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=30,
    )
    active = service.activate_assignment(
        "project-a",
        created.assignment_id,
        agent_id="agent-builder-1",
        timestamp=40,
    )
    service.complete_assignment(
        "project-a",
        active.assignment_id,
        agent_id="agent-builder-1",
        summary="Project A completed.",
        timestamp=50,
    )

    assert len(service.list_results("project-a")) == 1
    assert service.list_results("project-b") == ()
    assert service.list_handoffs("project-b") == ()
