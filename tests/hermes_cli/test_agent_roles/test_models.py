"""Specialized Agent Roles domain model tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import models as m


def test_current_schema_version_is_supported() -> None:
    assert m.CURRENT_SCHEMA_VERSION in m.SUPPORTED_SCHEMA_VERSIONS


def test_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(
        ValueError,
        match="schema version 999 not supported",
    ):
        m._validate_schema(999)


def test_generated_identifiers_have_expected_prefixes() -> None:
    assert m.new_role_id().startswith("role_")
    assert m.new_assignment_id().startswith("assign_")
    assert m.new_handoff_id().startswith("handoff_")
    assert m.new_result_id().startswith("result_")


def test_generated_identifiers_are_unique() -> None:
    assert m.new_assignment_id() != m.new_assignment_id()


def test_identifier_prefix_must_not_be_empty() -> None:
    with pytest.raises(
        ValueError,
        match="identifier prefix must not be empty",
    ):
        m._new_identifier("   ")


def test_builtin_role_values_are_stable() -> None:
    assert [role.value for role in m.BuiltinRole] == [
        "planner",
        "builder",
        "reviewer",
        "tester",
        "security",
        "documentation",
        "release",
    ]


def test_assignment_status_values_include_governed_states() -> None:
    expected = {
        m.AssignmentStatus.PENDING,
        m.AssignmentStatus.ASSIGNED,
        m.AssignmentStatus.ACTIVE,
        m.AssignmentStatus.HANDOFF_REQUESTED,
        m.AssignmentStatus.HANDED_OFF,
        m.AssignmentStatus.COMPLETED,
        m.AssignmentStatus.FAILED,
        m.AssignmentStatus.BLOCKED,
        m.AssignmentStatus.CANCELLED,
        m.AssignmentStatus.UNKNOWN,
    }

    assert expected.issubset(set(m.AssignmentStatus))


def test_role_capability_strips_text() -> None:
    capability = m.RoleCapability(
        capability_id=" planning ",
        description=" Create a bounded plan. ",
    )

    assert capability.capability_id == "planning"
    assert capability.description == "Create a bounded plan."


def test_role_capability_is_immutable() -> None:
    capability = m.RoleCapability(
        capability_id="planning",
        description="Create a plan.",
    )

    with pytest.raises(ValidationError):
        capability.required = False


def test_role_policy_normalises_and_deduplicates_values() -> None:
    policy = m.RolePolicy(
        allowed_risk_levels=(
            " low ",
            "low",
            "",
            "medium",
        ),
        allowed_paths=(
            "hermes_cli",
            " hermes_cli ",
            "tests",
        ),
    )

    assert policy.allowed_risk_levels == (
        "low",
        "medium",
    )
    assert policy.allowed_paths == (
        "hermes_cli",
        "tests",
    )


def test_role_policy_rejects_path_overlap() -> None:
    with pytest.raises(
        ValidationError,
        match="paths may not appear in both",
    ):
        m.RolePolicy(
            allowed_paths=("hermes_cli",),
            denied_paths=("hermes_cli",),
        )


def test_agent_role_rejects_duplicate_capability_ids() -> None:
    capability = m.RoleCapability(
        capability_id="planning",
        description="Create a plan.",
    )

    with pytest.raises(
        ValidationError,
        match="unique capability IDs",
    ):
        m.AgentRole(
            role_id="planner",
            name="Planner",
            description="Plans work.",
            capabilities=(
                capability,
                capability,
            ),
        )


def test_builtin_role_catalog_is_complete_and_stable() -> None:
    roles = m.builtin_agent_roles()

    assert len(roles) == 7
    assert tuple(role.role_id for role in roles) == tuple(
        role.value for role in m.BuiltinRole
    )
    assert all(role.built_in for role in roles)
    assert all(role.active for role in roles)


def test_builder_role_may_modify_repository() -> None:
    roles = {
        role.role_id: role
        for role in m.builtin_agent_roles()
    }

    builder = roles[m.BuiltinRole.BUILDER.value]

    assert builder.policy.may_modify_repository is True
    assert {
        capability.capability_id
        for capability in builder.capabilities
    } == {
        "code-change",
        "focused-testing",
    }


def test_release_role_requires_human_approval() -> None:
    roles = {
        role.role_id: role
        for role in m.builtin_agent_roles()
    }

    release = roles[m.BuiltinRole.RELEASE.value]

    assert release.policy.requires_human_approval is True
    assert release.policy.max_concurrent_assignments == 1


def test_pending_assignment_does_not_require_agent() -> None:
    assignment = m.Assignment(
        assignment_id="assign_1",
        project_id="hermes-platform",
        role_id="planner",
        status=m.AssignmentStatus.PENDING,
        created_at=10,
        updated_at=10,
    )

    assert assignment.assigned_agent_id is None


def test_active_assignment_requires_agent() -> None:
    with pytest.raises(
        ValidationError,
        match="active assignments require assigned_agent_id",
    ):
        m.Assignment(
            assignment_id="assign_1",
            project_id="hermes-platform",
            role_id="builder",
            status=m.AssignmentStatus.ACTIVE,
            created_at=10,
            updated_at=10,
        )


def test_assignment_normalises_required_capabilities() -> None:
    assignment = m.Assignment(
        assignment_id="assign_1",
        project_id="hermes-platform",
        role_id="builder",
        required_capabilities=(
            " code-change ",
            "code-change",
            "",
            "focused-testing",
        ),
        created_at=10,
        updated_at=10,
    )

    assert assignment.required_capabilities == (
        "code-change",
        "focused-testing",
    )


def test_assignment_rejects_reversed_timestamps() -> None:
    with pytest.raises(
        ValidationError,
        match="updated_at must not be earlier",
    ):
        m.Assignment(
            assignment_id="assign_1",
            project_id="hermes-platform",
            role_id="planner",
            created_at=20,
            updated_at=10,
        )


def test_assignment_is_immutable() -> None:
    assignment = m.Assignment(
        assignment_id="assign_1",
        project_id="hermes-platform",
        role_id="planner",
        created_at=10,
        updated_at=10,
    )

    with pytest.raises(ValidationError):
        assignment.status = m.AssignmentStatus.ACTIVE


def test_handoff_requires_different_roles() -> None:
    with pytest.raises(
        ValidationError,
        match="different role",
    ):
        m.AssignmentHandoff(
            handoff_id="handoff_1",
            assignment_id="assign_1",
            project_id="hermes-platform",
            from_role_id="builder",
            to_role_id="builder",
            reason=m.HandoffReason.STAGE_COMPLETE,
            summary="Builder completed implementation.",
            timestamp=10,
        )


def test_handoff_normalises_evidence_refs() -> None:
    handoff = m.AssignmentHandoff(
        handoff_id="handoff_1",
        assignment_id="assign_1",
        project_id="hermes-platform",
        from_role_id="builder",
        to_role_id="reviewer",
        reason=m.HandoffReason.REVIEW_REQUIRED,
        summary="Implementation is ready for review.",
        evidence_refs=(
            "pytest:focused",
            " pytest:focused ",
            "",
            "git:diff",
        ),
        timestamp=10,
    )

    assert handoff.evidence_refs == (
        "pytest:focused",
        "git:diff",
    )


def test_assignment_result_preserves_evidence() -> None:
    result = m.AssignmentResult(
        result_id="result_1",
        assignment_id="assign_1",
        project_id="hermes-platform",
        role_id="tester",
        outcome=m.AssignmentOutcome.SUCCEEDED,
        summary="Focused tests passed.",
        evidence_refs=(
            "pytest:agent-roles",
            " pytest:agent-roles ",
        ),
        completed_at=10,
    )

    assert result.evidence_refs == (
        "pytest:agent-roles",
    )
    assert result.outcome == m.AssignmentOutcome.SUCCEEDED


def test_negative_timestamps_fail_closed() -> None:
    with pytest.raises(
        ValidationError,
        match="completed_at must be a non-negative",
    ):
        m.AssignmentResult(
            result_id="result_1",
            assignment_id="assign_1",
            project_id="hermes-platform",
            role_id="tester",
            outcome=m.AssignmentOutcome.FAILED,
            summary="Tests failed.",
            completed_at=-1,
        )
