from __future__ import annotations

from hermes_prime_agent_worker import policy


def test_admit_requires_no_reason_codes():
    decision = policy.PolicyDecision.admit()
    assert decision.permitted
    assert decision.reason_codes == ()


def test_deny_requires_at_least_one_reason_code():
    import pytest

    with pytest.raises(ValueError):
        policy.PolicyDecision(permitted=False, reason_codes=())


def test_permitted_decision_rejects_reason_codes():
    import pytest

    with pytest.raises(ValueError):
        policy.PolicyDecision(permitted=True, reason_codes=("x",))


def test_evaluate_task_request_admits_when_all_conditions_met(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="inspect the repository",
    )
    assert decision.permitted


def test_evaluate_task_request_denies_workspace_outside_allowlist(
    worker_config_factory, tmp_path
):
    config = worker_config_factory()
    outside = tmp_path / "outside"
    outside.mkdir()
    decision = policy.evaluate_task_request(config, workspace=outside, task_text="hi")
    assert not decision.permitted
    assert policy.WORKSPACE_NOT_ALLOWLISTED in decision.reason_codes


def test_evaluate_task_request_denies_when_provider_inactive(worker_config_factory):
    config = worker_config_factory(provider_active=False)
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(config, workspace=workspace, task_text="hi")
    assert policy.PROVIDER_NOT_ACTIVE in decision.reason_codes


def test_evaluate_task_request_accumulates_multiple_reasons(
    worker_config_factory, tmp_path
):
    config = worker_config_factory(provider_active=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    decision = policy.evaluate_task_request(
        config, workspace=outside, task_text="sudo rm -rf /"
    )
    assert not decision.permitted
    assert policy.WORKSPACE_NOT_ALLOWLISTED in decision.reason_codes
    assert policy.PROVIDER_NOT_ACTIVE in decision.reason_codes
    assert policy.PRIVILEGED_COMMAND_DENIED in decision.reason_codes


def test_evaluate_task_request_denies_privileged_command(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config, workspace=workspace, task_text="please run sudo systemctl edit ollama"
    )
    assert policy.PRIVILEGED_COMMAND_DENIED in decision.reason_codes


def test_evaluate_task_request_denies_git_mutation_command(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config, workspace=workspace, task_text="git push origin main"
    )
    assert policy.GIT_MUTATION_DENIED in decision.reason_codes


def test_evaluate_task_request_denies_own_unit_reference(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="edit /etc/systemd/system/hermes-prime-agent-worker.service",
    )
    assert policy.SELF_UNIT_MUTATION_DENIED in decision.reason_codes


def test_evaluate_task_request_denies_mutation_tool_without_approval(
    worker_config_factory,
):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="edit a file",
        requested_tools=("write_file",),
        mutation_approved=False,
    )
    assert not decision.permitted
    assert policy.MUTATION_NOT_APPROVED in decision.reason_codes


def test_evaluate_task_request_admits_mutation_tool_with_approval(
    worker_config_factory,
):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="edit a file",
        requested_tools=("write_file",),
        mutation_approved=True,
    )
    assert decision.permitted


def test_evaluate_task_request_denies_unknown_tool(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="hi",
        requested_tools=("delete_universe",),
    )
    assert policy.UNKNOWN_TOOL_REQUESTED in decision.reason_codes


def test_evaluate_task_request_denies_concurrent_session_limit(worker_config_factory):
    config = worker_config_factory(max_concurrent_sessions=1)
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config, workspace=workspace, task_text="hi", active_session_count=1
    )
    assert policy.CONCURRENT_SESSION_LIMIT_EXCEEDED in decision.reason_codes


def test_evaluate_task_request_denies_during_cooldown(worker_config_factory):
    config = worker_config_factory(
        max_consecutive_failures_before_cooldown=2, cooldown_seconds_after_failure=60
    )
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="hi",
        consecutive_failures=2,
        seconds_since_last_failure=5.0,
    )
    assert policy.COOLDOWN_ACTIVE in decision.reason_codes


def test_evaluate_task_request_admits_after_cooldown_elapses(worker_config_factory):
    config = worker_config_factory(
        max_consecutive_failures_before_cooldown=2, cooldown_seconds_after_failure=10
    )
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config,
        workspace=workspace,
        task_text="hi",
        consecutive_failures=2,
        seconds_since_last_failure=100.0,
    )
    assert decision.permitted


def test_evaluate_task_request_denies_kill_switch(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    decision = policy.evaluate_task_request(
        config, workspace=workspace, task_text="hi", kill_switch_active=True
    )
    assert policy.KILL_SWITCH_ACTIVE in decision.reason_codes


def test_evaluate_doctor_fix_requires_approval():
    decision = policy.evaluate_doctor_fix(requested_fix=True, fix_approved=False)
    assert not decision.permitted
    assert policy.MUTATION_NOT_APPROVED in decision.reason_codes


def test_evaluate_doctor_fix_admits_with_approval():
    decision = policy.evaluate_doctor_fix(requested_fix=True, fix_approved=True)
    assert decision.permitted


def test_evaluate_doctor_fix_admits_read_only_by_default():
    decision = policy.evaluate_doctor_fix(requested_fix=False, fix_approved=False)
    assert decision.permitted


def test_is_privileged_command_detects_sudo():
    assert policy.is_privileged_command("sudo apt install foo")
    assert not policy.is_privileged_command("read the file please")


def test_is_git_mutation_command_detects_push_and_merge():
    assert policy.is_git_mutation_command("git push origin main")
    assert policy.is_git_mutation_command("git merge feature-branch")
    assert not policy.is_git_mutation_command("git status")
