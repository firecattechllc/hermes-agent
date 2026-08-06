from __future__ import annotations

import json

import pytest

from hermes_prime_agent_worker import cli


def _run_cli(capsys, config, args):
    env_overrides = {
        "HERMES_PRIME_AGENT_WORKER_EXECUTABLE": str(config.executable),
        "HERMES_PRIME_AGENT_WORKER_NODE_BIN_DIR": str(config.node_bin_dir),
        "HERMES_PRIME_AGENT_WORKER_HOME_DIR": str(config.home_dir),
        "HERMES_PRIME_AGENT_WORKER_WORKSPACE_ALLOWLIST": str(
            config.workspace_allowlist[0]
        ),
        "HERMES_PRIME_AGENT_WORKER_PROVIDER_ACTIVE": "true"
        if config.provider_active
        else "false",
    }
    import os

    old = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        exit_code = cli.main(args)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    captured = capsys.readouterr()
    return exit_code, captured.out


def test_cli_status_with_a_running_daemon_does_not_crash(
    worker_config_factory, set_fake_mode, capsys
):
    # Regression: DaemonSnapshot is a slots=True dataclass; the CLI must
    # not use `.__dict__` on it (slots dataclasses have none) -- this test
    # exercises status() with a populated, non-empty snapshot list, which
    # every other test in this suite leaves empty and so never caught it.
    config = worker_config_factory()
    set_fake_mode(config, "daemon_status")

    exit_code, out = _run_cli(capsys, config, ["status"])

    assert exit_code == 0
    payload = json.loads(out)
    assert payload[0]["pid"] == 424242
    assert payload[0]["health"] == "running"


def test_cli_validate_config(worker_config_factory, capsys):
    config = worker_config_factory()
    exit_code, out = _run_cli(capsys, config, ["validate-config"])
    assert exit_code == 0
    assert json.loads(out) == {"valid": True}


def test_cli_run_denied_returns_nonzero_exit(worker_config_factory, capsys):
    config = worker_config_factory(provider_active=False)
    exit_code, out = _run_cli(
        capsys,
        config,
        ["run", "--workspace", str(config.workspace_allowlist[0]), "--task", "hi"],
    )
    assert exit_code == 1
    payload = json.loads(out)
    assert payload["permitted"] is False


def test_cli_run_admitted_returns_zero_exit(
    worker_config_factory, set_fake_mode, capsys
):
    config = worker_config_factory()
    set_fake_mode(config, "json")
    exit_code, out = _run_cli(
        capsys,
        config,
        ["run", "--workspace", str(config.workspace_allowlist[0]), "--task", "hi"],
    )
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["permitted"] is True


def test_cli_emergency_stop(worker_config_factory, capsys):
    config = worker_config_factory()
    exit_code, out = _run_cli(capsys, config, ["emergency-stop", "--reason", "test"])
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["kill_switch"] is True
