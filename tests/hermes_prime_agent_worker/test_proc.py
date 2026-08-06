from __future__ import annotations

from pathlib import Path

import pytest

from hermes_prime_agent_worker import proc
from hermes_prime_agent_worker.proc import ProcInvocationError


def test_run_bounded_executes_and_captures_output(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    result = proc.run_bounded(config, [str(config.executable), "hello"], cwd=workspace)
    assert result.returncode == 0
    assert "ok: hello" in result.stdout
    assert not result.timed_out


def test_run_bounded_rejects_non_absolute_cwd(worker_config_factory):
    config = worker_config_factory()
    with pytest.raises(ProcInvocationError, match="absolute"):
        proc.run_bounded(config, [str(config.executable)], cwd=Path("relative"))


def test_run_bounded_rejects_argv_not_matching_configured_executable(
    worker_config_factory,
):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    with pytest.raises(ProcInvocationError, match="argv\\[0\\]"):
        proc.run_bounded(config, ["/bin/echo", "hi"], cwd=workspace)


def test_run_bounded_rejects_wrong_executable_basename(worker_config_factory, tmp_path):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    # Bypass config validation to exercise proc.py's own defense-in-depth check.
    fake_argv0 = str(tmp_path / "not-prime-agent")
    with pytest.raises(ProcInvocationError):
        proc.run_bounded(config, [fake_argv0], cwd=workspace)


def test_run_bounded_rejects_empty_argv(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    with pytest.raises(ProcInvocationError, match="empty"):
        proc.run_bounded(config, [], cwd=workspace)


def test_run_bounded_rejects_newline_in_argv(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    with pytest.raises(ProcInvocationError, match="newline"):
        proc.run_bounded(
            config, [str(config.executable), "hi\nsudo rm -rf /"], cwd=workspace
        )


def test_run_bounded_times_out_and_kills_process_group(
    worker_config_factory, set_fake_mode
):
    config = worker_config_factory(timeout_seconds=10)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "sleep")
    result = proc.run_bounded(
        config, [str(config.executable)], cwd=workspace, timeout_seconds=1.0
    )
    assert result.timed_out
    assert result.returncode != 0 or result.returncode is None


def test_run_bounded_truncates_large_output(worker_config_factory, set_fake_mode):
    config = worker_config_factory(max_output_bytes=1000)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "large")
    result = proc.run_bounded(config, [str(config.executable)], cwd=workspace)
    assert result.truncated
    assert len(result.stdout.encode("utf-8")) <= 1000


def test_run_bounded_never_exceeds_configured_timeout_ceiling(
    worker_config_factory, set_fake_mode
):
    config = worker_config_factory(timeout_seconds=10)
    workspace = config.workspace_allowlist[0]
    set_fake_mode(config, "sleep")
    # Even though the caller asks for a 60s budget, config.timeout_seconds
    # (10s) is a hard ceiling run_bounded must never exceed.
    result = proc.run_bounded(
        config, [str(config.executable)], cwd=workspace, timeout_seconds=60.0
    )
    assert result.duration_seconds < 20


def test_build_environment_excludes_host_environment(
    worker_config_factory, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/should-not-leak")
    config = worker_config_factory()
    env = proc.build_environment(config)
    assert "ANTHROPIC_API_KEY" not in env
    assert "SSH_AUTH_SOCK" not in env
    assert set(env.keys()) == {
        "PATH",
        "HOME",
        "XDG_DATA_HOME",
        "USER",
        "LANG",
        "PRIME_AGENT_INSTALLER_PLAIN",
    }


def test_build_environment_path_prioritizes_private_node_bin(worker_config_factory):
    config = worker_config_factory()
    env = proc.build_environment(config)
    assert env["PATH"].startswith(str(config.node_bin_dir))
