"""Stateless governed launch-contract builder tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli.agent_roles.launch import (
    LaunchContractStatus,
    LaunchWorkspaceMode,
)
from hermes_cli.agent_roles.launch_builder import (
    LaunchContractBuildError,
    LaunchContractBuilder,
)
from hermes_cli.agent_roles.models import AssignmentStatus


def _capability(capability_id: str) -> SimpleNamespace:
    return SimpleNamespace(capability_id=capability_id)


def _role(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "project_id": "hermes-platform",
        "role_id": "builder",
        "active": True,
        "capabilities": (
            _capability("code-change"),
            _capability("test-execution"),
        ),
        "policy": SimpleNamespace(
            allowed_risk_levels=("low", "medium", "high"),
            may_modify_repository=True,
            requires_human_approval=False,
            allowed_paths=("hermes_cli", "tests"),
            denied_paths=(".git", "secrets"),
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _assignment(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "assignment_id": "assign_1",
        "project_id": "hermes-platform",
        "role_id": "builder",
        "backlog_item_id": "backlog_1",
        "status": AssignmentStatus.ACCEPTED,
        "assigned_agent_id": "agent_codex",
        "required_capabilities": ("code-change",),
        "instructions": "Build the launch-contract derivation layer.",
        "created_at": 100,
        "updated_at": 110,
        "correlation_id": "corr_1",
        "causation_id": "cause_1",
        "version": 3,
        "metadata": {
            "risk_level": "medium",
            "requested_paths": (
                "hermes_cli/agent_roles",
                "tests/hermes_cli/test_agent_roles",
            ),
            "denied_paths": ("local-secrets",),
            "modifies_repository": True,
            "human_approved": False,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _build(**overrides: object):
    values: dict[str, object] = {
        "assignment": _assignment(),
        "role": _role(),
        "repository_root": "/repo/hermes-platform",
        "runtime": "hermes",
        "timestamp": 200,
        "engine": "codex",
        "provider": "openai-codex",
        "model": "auto",
        "base_ref": "step5-specialized-agent-roles",
    }
    values.update(overrides)
    return LaunchContractBuilder().build(**values)


def test_builds_ready_contract_from_accepted_assignment() -> None:
    contract = _build()

    assert contract.status == LaunchContractStatus.READY
    assert contract.contract_id == "launch_assign_1_v3"
    assert contract.assignment_id == "assign_1"
    assert contract.role_id == "builder"
    assert contract.agent_id == "agent_codex"
    assert contract.causation_id == "assign_1"


def test_repository_change_uses_isolated_workspace() -> None:
    contract = _build()

    assert (
        contract.workspace.mode
        == LaunchWorkspaceMode.ISOLATED_WRITE
    )
    assert contract.workspace.workspace_id == "workspace_assign_1"


def test_read_only_assignment_uses_read_only_workspace() -> None:
    assignment = _assignment(
        metadata={
            "risk_level": "low",
            "requested_paths": (),
            "modifies_repository": False,
            "human_approved": False,
        }
    )

    contract = _build(assignment=assignment)

    assert contract.status == LaunchContractStatus.READY
    assert contract.workspace.mode == LaunchWorkspaceMode.READ_ONLY
    assert contract.workspace.workspace_id is None


def test_nonaccepted_assignment_builds_blocked_contract() -> None:
    assignment = _assignment(status=AssignmentStatus.ASSIGNED)

    contract = _build(assignment=assignment)

    assert contract.status == LaunchContractStatus.BLOCKED
    assert contract.blocked_reasons == (
        "assignment must be accepted before launch",
    )


def test_inactive_role_builds_blocked_contract() -> None:
    contract = _build(role=_role(active=False))

    assert contract.status == LaunchContractStatus.BLOCKED
    assert "role is inactive" in contract.blocked_reasons


def test_missing_capability_builds_blocked_contract() -> None:
    assignment = _assignment(
        required_capabilities=("security-review",)
    )

    contract = _build(assignment=assignment)

    assert contract.status == LaunchContractStatus.BLOCKED
    assert contract.blocked_reasons == (
        "role lacks required capabilities: security-review",
    )


def test_human_approval_policy_builds_blocked_contract() -> None:
    policy = _role().policy
    policy.requires_human_approval = True

    contract = _build(
        role=_role(policy=policy),
    )

    assert contract.status == LaunchContractStatus.BLOCKED
    assert "human approval is required" in contract.blocked_reasons


def test_denied_path_builds_blocked_contract() -> None:
    assignment = _assignment(
        metadata={
            "risk_level": "medium",
            "requested_paths": ("secrets/token.txt",),
            "modifies_repository": True,
            "human_approved": False,
        }
    )

    contract = _build(assignment=assignment)

    assert contract.status == LaunchContractStatus.BLOCKED
    assert contract.blocked_reasons == (
        "requested path is denied by role policy: "
        "secrets/token.txt",
    )


def test_outside_allowed_path_builds_blocked_contract() -> None:
    assignment = _assignment(
        metadata={
            "risk_level": "medium",
            "requested_paths": ("deployment/prod.yml",),
            "modifies_repository": True,
            "human_approved": False,
        }
    )

    contract = _build(assignment=assignment)

    assert contract.status == LaunchContractStatus.BLOCKED
    assert contract.blocked_reasons == (
        "requested path is outside role policy: "
        "deployment/prod.yml",
    )


def test_source_identity_mismatch_fails_closed() -> None:
    with pytest.raises(
        LaunchContractBuildError,
        match="role_id does not match",
    ):
        _build(role=_role(role_id="reviewer"))


def test_missing_agent_fails_closed() -> None:
    with pytest.raises(
        LaunchContractBuildError,
        match="assigned_agent_id is required",
    ):
        _build(
            assignment=_assignment(assigned_agent_id=None)
        )


def test_malformed_metadata_fails_closed() -> None:
    with pytest.raises(
        LaunchContractBuildError,
        match="requested_paths",
    ):
        _build(
            assignment=_assignment(
                metadata={
                    "risk_level": "medium",
                    "requested_paths": "hermes_cli",
                    "modifies_repository": True,
                    "human_approved": False,
                }
            )
        )


def test_builder_does_not_mutate_assignment_or_role() -> None:
    assignment = _assignment()
    role = _role()
    original_assignment_metadata = dict(assignment.metadata)
    original_denied_paths = role.policy.denied_paths

    _build(assignment=assignment, role=role)

    assert assignment.metadata == original_assignment_metadata
    assert role.policy.denied_paths == original_denied_paths


def test_explicit_runtime_selection_is_preserved() -> None:
    contract = _build(
        runtime="hermes-agent",
        engine="codex-titan",
        provider="openai-codex",
        model="gpt-5.6-codex",
        environment=(("MODE", "governed"),),
    )

    assert contract.environment.runtime == "hermes-agent"
    assert contract.environment.engine == "codex-titan"
    assert contract.environment.provider == "openai-codex"
    assert contract.environment.model == "gpt-5.6-codex"
    assert contract.environment.environment == (
        ("MODE", "governed"),
    )
