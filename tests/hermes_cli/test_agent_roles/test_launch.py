"""Immutable governed launch-contract model tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
    LaunchEnvironment,
    LaunchPolicy,
    LaunchWorkspace,
    LaunchWorkspaceMode,
)


def _contract(**overrides: object) -> LaunchContract:
    values: dict[str, object] = {
        "contract_id": "launch_1",
        "project_id": "hermes-platform",
        "assignment_id": "assign_1",
        "role_id": "builder",
        "agent_id": "agent_codex",
        "backlog_item_id": "backlog_1",
        "instructions": "Implement the governed launch contract.",
        "created_at": 100,
        "workspace": LaunchWorkspace(
            mode=LaunchWorkspaceMode.ISOLATED_WRITE,
            repository_root="/repo/hermes-platform",
            workspace_id="workspace_assign_1",
            base_ref="step5-specialized-agent-roles",
        ),
        "policy": LaunchPolicy(
            risk_level="medium",
            modifies_repository=True,
            allowed_paths=(
                "hermes_cli/agent_roles/launch.py",
                "tests/hermes_cli/test_agent_roles/test_launch.py",
            ),
            denied_paths=(".git",),
            required_capabilities=("code-change",),
        ),
        "environment": LaunchEnvironment(
            runtime="hermes",
            engine="codex",
            provider="openai-codex",
            model="auto",
        ),
    }
    values.update(overrides)
    return LaunchContract(**values)


def test_launch_contract_is_immutable() -> None:
    contract = _contract()

    with pytest.raises(ValidationError):
        contract.role_id = "reviewer"


def test_launch_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        LaunchEnvironment(
            runtime="hermes",
            secret_token="do-not-store",
        )


def test_launch_contract_normalises_text() -> None:
    contract = _contract(
        contract_id=" launch_1 ",
        instructions="  Build the contract.  ",
    )

    assert contract.contract_id == "launch_1"
    assert contract.instructions == "Build the contract."


def test_launch_policy_normalises_and_deduplicates() -> None:
    policy = LaunchPolicy(
        risk_level=" MEDIUM ",
        modifies_repository=True,
        allowed_paths=(
            " hermes_cli ",
            "hermes_cli",
        ),
        required_capabilities=(
            " code-change ",
            "code-change",
        ),
    )

    assert policy.risk_level == "medium"
    assert policy.allowed_paths == ("hermes_cli",)
    assert policy.required_capabilities == ("code-change",)


def test_launch_policy_rejects_path_overlap() -> None:
    with pytest.raises(
        ValidationError,
        match="both allowed and denied",
    ):
        LaunchPolicy(
            risk_level="medium",
            allowed_paths=("hermes_cli",),
            denied_paths=("hermes_cli",),
        )


def test_repository_change_requires_allowed_paths() -> None:
    with pytest.raises(
        ValidationError,
        match="requires allowed_paths",
    ):
        LaunchPolicy(
            risk_level="medium",
            modifies_repository=True,
        )


def test_isolated_write_workspace_requires_id() -> None:
    with pytest.raises(
        ValidationError,
        match="requires workspace_id",
    ):
        LaunchWorkspace(
            mode=LaunchWorkspaceMode.ISOLATED_WRITE,
            repository_root="/repo/hermes-platform",
        )


def test_repository_change_requires_isolated_workspace() -> None:
    with pytest.raises(
        ValidationError,
        match="requires isolated_write",
    ):
        _contract(
            workspace=LaunchWorkspace(
                mode=LaunchWorkspaceMode.READ_ONLY,
                repository_root="/repo/hermes-platform",
            )
        )


def test_ready_contract_rejects_blocked_reasons() -> None:
    with pytest.raises(
        ValidationError,
        match="may not have blocked_reasons",
    ):
        _contract(
            status=LaunchContractStatus.READY,
            blocked_reasons=("Provider unavailable",),
        )


def test_blocked_contract_requires_reason() -> None:
    with pytest.raises(
        ValidationError,
        match="requires blocked_reasons",
    ):
        _contract(
            status=LaunchContractStatus.BLOCKED,
        )


def test_blocked_contract_preserves_reasons() -> None:
    contract = _contract(
        status=LaunchContractStatus.BLOCKED,
        blocked_reasons=(
            " Provider unavailable ",
            "Provider unavailable",
            "Workspace unavailable",
        ),
    )

    assert contract.blocked_reasons == (
        "Provider unavailable",
        "Workspace unavailable",
    )


def test_unknown_launch_schema_fails_closed() -> None:
    with pytest.raises(
        ValidationError,
        match="not supported",
    ):
        _contract(schema_version=99)


def test_environment_rejects_duplicate_keys() -> None:
    with pytest.raises(
        ValidationError,
        match="duplicate environment key",
    ):
        LaunchEnvironment(
            runtime="hermes",
            environment=(
                ("MODE", "safe"),
                (" MODE ", "unsafe"),
            ),
        )
