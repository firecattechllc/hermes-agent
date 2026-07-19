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
