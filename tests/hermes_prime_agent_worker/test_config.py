from __future__ import annotations

from pathlib import Path

import pytest

from hermes_prime_agent_worker.config import (
    PrimeAgentWorkerConfig,
    PrimeAgentWorkerConfigError,
    validate_no_mac_dependency,
)


def test_valid_config_accepted(worker_config_factory):
    config = worker_config_factory()
    assert config.provider_active is True
    assert config.workspace_allowlist


def test_rejects_relative_home_dir(worker_config_factory):
    with pytest.raises(PrimeAgentWorkerConfigError, match="absolute"):
        worker_config_factory(home_dir=Path("relative/path"))


def test_rejects_empty_workspace_allowlist(worker_config_factory):
    with pytest.raises(PrimeAgentWorkerConfigError, match="workspace_allowlist"):
        worker_config_factory(workspace_allowlist=())


def test_rejects_wrong_executable_name(worker_config_factory, tmp_path):
    bad = tmp_path / "not-prime-agent"
    bad.write_text("#!/bin/sh\necho hi\n")
    with pytest.raises(PrimeAgentWorkerConfigError, match="executable must be named"):
        worker_config_factory(executable=bad)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("max_turns", 0, "max_turns"),
        ("max_turns", 13, "max_turns"),
        ("max_tokens", 0, "max_tokens"),
        ("timeout_seconds", 5, "timeout_seconds"),
        ("timeout_seconds", 5000, "timeout_seconds"),
        ("max_concurrent_sessions", 0, "max_concurrent_sessions"),
        ("max_concurrent_sessions", 10, "max_concurrent_sessions"),
    ],
)
def test_rejects_out_of_bounds_values(worker_config_factory, field, value, message):
    with pytest.raises(PrimeAgentWorkerConfigError, match=message):
        worker_config_factory(**{field: value})


def test_rejects_overlapping_tool_lists(worker_config_factory):
    with pytest.raises(PrimeAgentWorkerConfigError, match="disjoint"):
        worker_config_factory(
            allowed_tools=("read_file",), mutation_tools=("read_file",)
        )


def test_rejects_mac_users_path(worker_config_factory, tmp_path):
    mac_path = Path("/Users/matthewcallaham/somewhere")
    with pytest.raises(PrimeAgentWorkerConfigError, match="Mac dependency"):
        worker_config_factory(workspace_allowlist=(mac_path,))


def test_rejects_host_docker_internal(worker_config_factory):
    with pytest.raises(PrimeAgentWorkerConfigError, match="Mac dependency"):
        worker_config_factory(
            allowed_network_endpoints=("http://host.docker.internal:8791",)
        )


def test_is_within_allowlist(worker_config_factory):
    config = worker_config_factory()
    workspace = config.workspace_allowlist[0]
    assert config.is_within_allowlist(workspace / "subdir" / "file.txt")
    assert not config.is_within_allowlist(config.home_dir.parent / "outside")


def test_xdg_local_share_path_is_not_a_mac_dependency_false_positive():
    # Regression: ~/.local/share/... is the ordinary Linux XDG data
    # directory convention (this is exactly where Prime Agent's own
    # private Node.js runtime lives), not a Mac mDNS hostname. It must
    # not trip the *.local hostname guard.
    violations = validate_no_mac_dependency({
        "node_bin_dir": "/var/lib/hermes-prime-agent/.local/share/prime-agent-node/current/bin"
    })
    assert violations == ()


def test_actual_mdns_mac_hostname_is_detected():
    violations = validate_no_mac_dependency({
        "endpoint": "http://some-other-host.local:8080"
    })
    assert len(violations) == 1
    assert "mDNS Mac hostname" in violations[0]


def test_worker_config_accepts_real_titan_xdg_paths(worker_config_factory, tmp_path):
    # End-to-end regression for the same false positive at the
    # PrimeAgentWorkerConfig level, matching the real Titan deployment
    # layout under /var/lib/hermes-prime-agent/.local/share/...
    xdg_style_node_bin = (
        tmp_path / "home" / ".local" / "share" / "prime-agent-node" / "bin"
    )
    xdg_style_node_bin.mkdir(parents=True)
    config = worker_config_factory(node_bin_dir=xdg_style_node_bin)
    assert config.node_bin_dir == xdg_style_node_bin


def test_validate_no_mac_dependency_reports_all_violations():
    violations = validate_no_mac_dependency({
        "a": "/Users/x/y",
        "b": "matthews-macbook-air lives here",
    })
    # "a" trips the /Users/ path check; "b" trips both the known-identity
    # check and the "macbook" hostname-marker check -- every applicable
    # violation is reported, not just the first one found.
    assert len(violations) >= 2
    assert any("a" in v and "Mac filesystem path" in v for v in violations)
    assert any("b" in v for v in violations)


def test_from_env_requires_home_dir(tmp_path):
    env = {
        "HERMES_PRIME_AGENT_WORKER_EXECUTABLE": str(tmp_path / "prime-agent"),
        "HERMES_PRIME_AGENT_WORKER_NODE_BIN_DIR": str(tmp_path / "node" / "bin"),
        "HERMES_PRIME_AGENT_WORKER_WORKSPACE_ALLOWLIST": str(tmp_path / "workspace"),
    }
    with pytest.raises(PrimeAgentWorkerConfigError, match="HOME_DIR"):
        PrimeAgentWorkerConfig.from_env(env)


def test_from_env_builds_valid_config(tmp_path):
    (tmp_path / "prime-agent").write_text("#!/bin/sh\n")
    env = {
        "HERMES_PRIME_AGENT_WORKER_EXECUTABLE": str(tmp_path / "prime-agent"),
        "HERMES_PRIME_AGENT_WORKER_NODE_BIN_DIR": str(tmp_path / "node" / "bin"),
        "HERMES_PRIME_AGENT_WORKER_HOME_DIR": str(tmp_path / "home"),
        "HERMES_PRIME_AGENT_WORKER_WORKSPACE_ALLOWLIST": str(tmp_path / "workspace"),
    }
    config = PrimeAgentWorkerConfig.from_env(env)
    assert config.provider_active is False
    assert config.provider == "titan-omniroute"
    assert config.model == "lightweight"
