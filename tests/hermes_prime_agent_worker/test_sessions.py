from __future__ import annotations

import json

from hermes_prime_agent_worker import policy
from hermes_prime_agent_worker.sessions import PrimeAgentWorker, _task_succeeded


def test_run_task_denied_outside_workspace_never_spawns_process(
    worker_config_factory, tmp_path
):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    outside = tmp_path / "outside"
    outside.mkdir()

    result = worker.run_task(workspace=outside, task_text="inspect the repo")

    assert not result.permitted
    assert policy.WORKSPACE_NOT_ALLOWLISTED in result.reason_codes
    assert result.returncode is None
    assert result.evidence_entry is not None


def test_run_task_denied_when_provider_not_active(worker_config_factory):
    config = worker_config_factory(provider_active=False)
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]

    result = worker.run_task(workspace=workspace, task_text="inspect the repo")

    assert not result.permitted
    assert policy.PROVIDER_NOT_ACTIVE in result.reason_codes


def test_run_task_admitted_executes_subprocess(worker_config_factory, set_fake_mode):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "json")

    result = worker.run_task(workspace=workspace, task_text="inspect the repo")

    assert result.permitted
    assert result.returncode == 0
    assert "agent_end" in result.stdout


def test_run_task_always_passes_offline_flag(worker_config_factory, set_fake_mode):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "json")

    worker.run_task(workspace=workspace, task_text="inspect the repo")

    last_argv = (config.home_dir / ".fake_prime_agent_last_argv").read_text()
    assert "--offline" in last_argv.split()


def test_run_task_denied_for_privileged_command(worker_config_factory):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]

    result = worker.run_task(workspace=workspace, task_text="sudo rm -rf /")

    assert not result.permitted
    assert policy.PRIVILEGED_COMMAND_DENIED in result.reason_codes


def test_run_task_writes_evidence_for_both_denial_and_success(
    worker_config_factory, set_fake_mode
):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "json")

    worker.run_task(workspace=workspace, task_text="sudo rm -rf /")
    worker.run_task(workspace=workspace, task_text="inspect the repo")

    entries = worker._evidence.read_all()
    statuses = [e["record"]["status"] for e in entries]
    assert "denied" in statuses
    assert "succeeded" in statuses
    assert worker._evidence.verify_chain()


def test_run_task_failure_tracked_for_cooldown(worker_config_factory, set_fake_mode):
    config = worker_config_factory(
        max_consecutive_failures_before_cooldown=2, cooldown_seconds_after_failure=3600
    )
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "fail")

    worker.run_task(workspace=workspace, task_text="do a thing")
    worker.run_task(workspace=workspace, task_text="do a thing")
    result = worker.run_task(workspace=workspace, task_text="do a thing")

    assert not result.permitted
    assert policy.COOLDOWN_ACTIVE in result.reason_codes


def test_run_task_success_resets_failure_count(worker_config_factory, set_fake_mode):
    config = worker_config_factory(
        max_consecutive_failures_before_cooldown=2, cooldown_seconds_after_failure=3600
    )
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]

    set_fake_mode(config, "fail")
    worker.run_task(workspace=workspace, task_text="do a thing")

    set_fake_mode(config, "json")
    worker.run_task(workspace=workspace, task_text="do a thing")

    set_fake_mode(config, "fail")
    result = worker.run_task(workspace=workspace, task_text="do a thing")

    # Only one consecutive failure since the success reset the counter, so
    # cooldown (threshold 2) should not yet be active.
    assert policy.COOLDOWN_ACTIVE not in result.reason_codes


def test_task_succeeded_false_for_nonzero_returncode():
    assert _task_succeeded(1, False, '{"type":"agent_end"}') is False


def test_task_succeeded_false_for_timeout():
    assert _task_succeeded(0, True, '{"type":"agent_end"}') is False


def test_task_succeeded_true_for_clean_json_transcript():
    stdout = '{"type":"session","id":"x"}\n{"type":"agent_end","messages":[]}\n'
    assert _task_succeeded(0, False, stdout) is True


def test_task_succeeded_false_when_exit_zero_but_auto_retry_failed():
    # Regression for the real, observed Prime Agent / OmniRoute behavior:
    # process exit code 0 with a failed model call buried in the
    # transcript must not be counted as success.
    stdout = (
        '{"type":"session","id":"x"}\n'
        '{"type":"message_end","message":{"stopReason":"error"}}\n'
        '{"type":"auto_retry_end","success":false,"attempt":3}\n'
    )
    assert _task_succeeded(0, False, stdout) is False


def test_task_succeeded_false_for_empty_or_unparseable_output():
    assert _task_succeeded(0, False, "") is False
    assert _task_succeeded(0, False, "not json at all") is False


def test_run_task_treats_exit_zero_model_failure_as_a_real_failure_for_cooldown(
    worker_config_factory, set_fake_mode
):
    config = worker_config_factory(
        max_consecutive_failures_before_cooldown=2, cooldown_seconds_after_failure=3600
    )
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "json_exit_zero_but_model_failed")

    first = worker.run_task(workspace=workspace, task_text="do a thing")
    worker.run_task(workspace=workspace, task_text="do a thing")
    third = worker.run_task(workspace=workspace, task_text="do a thing")

    # The subprocess itself succeeded (permitted, returncode 0) each time
    # -- but the cooldown mechanism must still have engaged because the
    # model call failed inside the transcript.
    assert first.permitted and first.returncode == 0
    assert not third.permitted
    assert policy.COOLDOWN_ACTIVE in third.reason_codes


def test_emergency_stop_sets_kill_switch_and_blocks_future_tasks(worker_config_factory):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    workspace = config.workspace_allowlist[0]

    worker.emergency_stop(reason="operator requested immediate stop")

    assert worker.is_kill_switch_active()
    result = worker.run_task(workspace=workspace, task_text="inspect the repo")
    assert not result.permitted
    assert policy.KILL_SWITCH_ACTIVE in result.reason_codes


def test_kill_switch_survives_new_worker_instance(worker_config_factory):
    config = worker_config_factory()
    worker_a = PrimeAgentWorker(config)
    worker_a.emergency_stop(reason="stop")

    worker_b = PrimeAgentWorker(config)
    assert worker_b.is_kill_switch_active()


def test_doctor_fix_denied_without_approval(worker_config_factory):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    result = worker.doctor(fix=True, fix_approved=False)
    assert result["permitted"] is False


def test_doctor_read_only_admitted_by_default(worker_config_factory):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    result = worker.doctor(fix=False)
    assert result["permitted"] is True


def test_status_parses_empty_daemon_state(worker_config_factory):
    config = worker_config_factory()
    worker = PrimeAgentWorker(config)
    # The fake script's default ("echo") mode does not emit JSON, so
    # status() must fall back to an empty/unknown snapshot rather than
    # raising.
    snapshots = worker.status()
    assert isinstance(snapshots, tuple)
