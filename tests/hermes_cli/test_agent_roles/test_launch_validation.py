"""Independent launch-contract validation tests."""

from __future__ import annotations

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
    LaunchEnvironment,
    LaunchPolicy,
    LaunchWorkspace,
    LaunchWorkspaceMode,
)
from hermes_cli.agent_roles.launch_validation import (
    LaunchContractValidator,
    LaunchValidationCode,
    LaunchValidationSeverity,
    RuntimeCompatibility,
)


def _contract(**overrides: object) -> LaunchContract:
    values: dict[str, object] = {
        "contract_id": "launch_1",
        "project_id": "hermes-platform",
        "assignment_id": "assign_1",
        "role_id": "builder",
        "agent_id": "agent_codex",
        "backlog_item_id": "backlog_1",
        "status": LaunchContractStatus.READY,
        "instructions": "Validate the governed launch boundary.",
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
            human_approved=False,
            allowed_paths=("hermes_cli/agent_roles",),
            denied_paths=(".git",),
            required_capabilities=(
                "code-change",
                "test-execution",
            ),
        ),
        "environment": LaunchEnvironment(
            runtime="hermes-agent",
            engine="codex",
            provider="openai-codex",
            model="auto",
            environment=(("MODE", "governed"),),
        ),
    }
    values.update(overrides)
    return LaunchContract(**values)


def _compatibility(
    **overrides: object,
) -> RuntimeCompatibility:
    values: dict[str, object] = {
        "runtime": "hermes-agent",
        "supported_engines": ("codex", "codex-titan"),
        "supported_providers": ("openai-codex",),
        "supported_models": ("auto", "gpt-5.6-codex"),
        "capabilities": (
            "code-change",
            "test-execution",
        ),
        "allowed_environment_keys": ("MODE",),
        "required_environment_keys": ("MODE",),
        "supports_repository_write": True,
        "supports_isolated_workspace": True,
        "requires_base_ref_for_write": True,
    }
    values.update(overrides)
    return RuntimeCompatibility(**values)


def _codes(result) -> tuple[LaunchValidationCode, ...]:
    return tuple(issue.code for issue in result.issues)


def test_compatible_contract_is_valid() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(),
    )

    assert result.valid is True
    assert result.issues == ()
    assert result.errors == ()
    assert result.warnings == ()


def test_validation_result_is_immutable() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(),
    )

    try:
        result.valid = False
    except Exception:
        pass
    else:
        raise AssertionError("validation result must be immutable")


def test_blocked_contract_is_invalid() -> None:
    contract = _contract(
        status=LaunchContractStatus.BLOCKED,
        blocked_reasons=("Provider unavailable",),
    )

    result = LaunchContractValidator().validate(
        contract,
        _compatibility(),
    )

    assert result.valid is False
    assert _codes(result) == (
        LaunchValidationCode.CONTRACT_BLOCKED,
    )


def test_runtime_mismatch_is_reported() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(runtime="different-runtime"),
    )

    assert result.valid is False
    assert LaunchValidationCode.RUNTIME_MISMATCH in _codes(
        result
    )


def test_unsupported_runtime_selections_are_reported() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(
            supported_engines=("other-engine",),
            supported_providers=("other-provider",),
            supported_models=("other-model",),
        ),
    )

    assert _codes(result) == (
        LaunchValidationCode.ENGINE_UNSUPPORTED,
        LaunchValidationCode.PROVIDER_UNSUPPORTED,
        LaunchValidationCode.MODEL_UNSUPPORTED,
    )


def test_missing_capabilities_are_reported_in_order() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(capabilities=()),
    )

    assert _codes(result) == (
        LaunchValidationCode.CAPABILITY_MISSING,
        LaunchValidationCode.CAPABILITY_MISSING,
    )
    assert "code-change" in result.issues[0].message
    assert "test-execution" in result.issues[1].message


def test_repository_write_support_is_required() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(
            supports_repository_write=False,
            supports_isolated_workspace=False,
        ),
    )

    assert _codes(result) == (
        LaunchValidationCode.REPOSITORY_WRITE_UNSUPPORTED,
        LaunchValidationCode.ISOLATED_WORKSPACE_UNSUPPORTED,
    )


def test_write_contract_requires_base_ref_when_configured() -> None:
    workspace = LaunchWorkspace(
        mode=LaunchWorkspaceMode.ISOLATED_WRITE,
        repository_root="/repo/hermes-platform",
        workspace_id="workspace_assign_1",
    )
    result = LaunchContractValidator().validate(
        _contract(workspace=workspace),
        _compatibility(),
    )

    assert _codes(result) == (
        LaunchValidationCode.BASE_REF_REQUIRED,
    )


def test_runtime_can_allow_write_without_base_ref() -> None:
    workspace = LaunchWorkspace(
        mode=LaunchWorkspaceMode.ISOLATED_WRITE,
        repository_root="/repo/hermes-platform",
        workspace_id="workspace_assign_1",
    )
    result = LaunchContractValidator().validate(
        _contract(workspace=workspace),
        _compatibility(
            requires_base_ref_for_write=False,
        ),
    )

    assert result.valid is True


def test_high_risk_contract_requires_human_approval() -> None:
    policy = LaunchPolicy(
        risk_level="high",
        modifies_repository=True,
        human_approved=False,
        allowed_paths=("hermes_cli",),
        required_capabilities=("code-change",),
    )
    result = LaunchContractValidator().validate(
        _contract(policy=policy),
        _compatibility(),
    )

    assert LaunchValidationCode.HUMAN_APPROVAL_MISSING in (
        _codes(result)
    )


def test_approved_high_risk_contract_is_valid() -> None:
    policy = LaunchPolicy(
        risk_level="high",
        modifies_repository=True,
        human_approved=True,
        allowed_paths=("hermes_cli",),
        required_capabilities=("code-change",),
    )
    result = LaunchContractValidator().validate(
        _contract(policy=policy),
        _compatibility(),
    )

    assert result.valid is True


def test_unsupported_environment_key_is_reported() -> None:
    environment = LaunchEnvironment(
        runtime="hermes-agent",
        engine="codex",
        provider="openai-codex",
        model="auto",
        environment=(
            ("MODE", "governed"),
            ("SECRET_TOKEN", "forbidden"),
        ),
    )
    result = LaunchContractValidator().validate(
        _contract(environment=environment),
        _compatibility(),
    )

    assert _codes(result) == (
        LaunchValidationCode.ENVIRONMENT_KEY_UNSUPPORTED,
    )


def test_required_environment_key_is_reported() -> None:
    environment = LaunchEnvironment(
        runtime="hermes-agent",
        engine="codex",
        provider="openai-codex",
        model="auto",
    )
    result = LaunchContractValidator().validate(
        _contract(environment=environment),
        _compatibility(),
    )

    assert _codes(result) == (
        LaunchValidationCode.ENVIRONMENT_KEY_REQUIRED,
    )


def test_empty_supported_selection_is_unrestricted() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(
            supported_engines=(),
            supported_providers=(),
            supported_models=(),
        ),
    )

    assert result.valid is True


def test_validate_many_preserves_input_order() -> None:
    first = _contract(contract_id="launch_1")
    second = _contract(contract_id="launch_2")

    results = LaunchContractValidator.validate_many(
        (first, second),
        _compatibility(),
    )

    assert tuple(
        result.contract_id for result in results
    ) == ("launch_1", "launch_2")
    assert all(result.valid for result in results)


def test_diagnostics_have_stable_severity_and_fields() -> None:
    result = LaunchContractValidator().validate(
        _contract(),
        _compatibility(runtime="other-runtime"),
    )

    issue = result.issues[0]

    assert issue.severity == LaunchValidationSeverity.ERROR
    assert issue.code == LaunchValidationCode.RUNTIME_MISMATCH
    assert issue.field == "environment.runtime"
