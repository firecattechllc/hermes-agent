"""Tests for the fail-closed Tailscale fleet connectivity checker.

scripts/fleet_connectivity_check.py is read-only and non-destructive: it
only ever runs `tailscale status --json` (never mutates state). These tests
inject a synthetic `status` dict rather than depending on a real tailnet, so
they are deterministic and safe to run in CI.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from fleet_connectivity_check import (  # noqa: E402
    FleetConnectivityCheckError,
    _load_dns_identity_from_config,
    _normalize_dns_identity,
    check_node_connectivity,
    main,
)

ONLINE_PEER_STATUS = {
    "BackendState": "Running",
    "Self": {
        "DNSName": "self-node.example.ts.net.",
        "HostName": "self-node",
        "Online": True,
    },
    "Peer": {
        "peer-key-1": {
            "DNSName": "hydra-titan.example.ts.net.",
            "HostName": "hydra-titan",
            "Online": True,
        },
        "peer-key-2": {
            "DNSName": "hydra-live.example.ts.net.",
            "HostName": "hydra-VMware20-1",
            "Online": False,
        },
    },
}


# ── check_node_connectivity ──────────────────────────────────────────


def test_online_peer_is_verified():
    result = check_node_connectivity(
        "hydra-titan.example.ts.net", status=ONLINE_PEER_STATUS
    )
    assert result.verified is True
    assert result.reason == "ok"
    assert result.peer_online is True
    assert result.peer_hostname == "hydra-titan"


def test_offline_peer_is_not_verified():
    result = check_node_connectivity(
        "hydra-live.example.ts.net", status=ONLINE_PEER_STATUS
    )
    assert result.verified is False
    assert result.reason == "node_offline"
    assert result.peer_found is True
    assert result.peer_online is False


def test_unknown_node_is_not_verified():
    result = check_node_connectivity(
        "nonexistent.example.ts.net", status=ONLINE_PEER_STATUS
    )
    assert result.verified is False
    assert result.reason == "node_not_found_in_tailnet"
    assert result.peer_found is False


def test_self_peer_matches():
    result = check_node_connectivity(
        "self-node.example.ts.net", status=ONLINE_PEER_STATUS
    )
    assert result.verified is True
    assert result.reason == "ok"


def test_matches_by_hostname_case_insensitive():
    result = check_node_connectivity("HYDRA-TITAN", status=ONLINE_PEER_STATUS)
    assert result.verified is True


def test_expected_hostname_mismatch_fails_closed():
    result = check_node_connectivity(
        "hydra-titan.example.ts.net",
        expected_hostname="wrong-hostname",
        status=ONLINE_PEER_STATUS,
    )
    assert result.verified is False
    assert result.reason == "hostname_mismatch"


def test_missing_backend_state_fails_closed():
    result = check_node_connectivity("hydra-titan.example.ts.net", status={})
    assert result.verified is False
    assert result.reason == "tailscale_backend_not_running"


def test_backend_not_running_fails_closed():
    result = check_node_connectivity(
        "hydra-titan.example.ts.net", status={"BackendState": "Stopped"}
    )
    assert result.verified is False
    assert result.reason == "tailscale_backend_not_running"


def test_dns_identity_normalization_strips_trailing_dot_and_case():
    assert (
        _normalize_dns_identity("Hydra-Titan.Example.TS.NET.")
        == "hydra-titan.example.ts.net"
    )


def test_tailscale_unavailable_fails_closed(monkeypatch):
    import fleet_connectivity_check as module

    monkeypatch.setattr(module, "_run_tailscale_status", lambda: None)
    result = check_node_connectivity("hydra-titan.example.ts.net")
    assert result.verified is False
    assert result.reason == "tailscale_unavailable_or_not_running"


# ── _load_dns_identity_from_config ───────────────────────────────────


def test_load_dns_identity_from_config_success(tmp_path):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text(
        json.dumps({
            "targets": [
                {
                    "node_id": "node-titan",
                    "tailnet_dns_identity": "hydra-titan.example.ts.net",
                },
            ]
        })
    )
    identity = _load_dns_identity_from_config(config_path, "titan")
    assert identity == "hydra-titan.example.ts.net"


def test_load_dns_identity_rejects_placeholder(tmp_path):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text(
        json.dumps({
            "targets": [
                {
                    "node_id": "node-titan",
                    "tailnet_dns_identity": "replace-with-verified-titan.ts.net",
                },
            ]
        })
    )
    with pytest.raises(FleetConnectivityCheckError, match="placeholder"):
        _load_dns_identity_from_config(config_path, "titan")


def test_load_dns_identity_rejects_missing_target(tmp_path):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text(json.dumps({"targets": []}))
    with pytest.raises(FleetConnectivityCheckError, match="no target matching"):
        _load_dns_identity_from_config(config_path, "titan")


def test_load_dns_identity_rejects_ambiguous_target(tmp_path):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text(
        json.dumps({
            "targets": [
                {"node_id": "node-titan-a", "tailnet_dns_identity": "a.example.ts.net"},
                {"node_id": "node-titan-b", "tailnet_dns_identity": "b.example.ts.net"},
            ]
        })
    )
    with pytest.raises(FleetConnectivityCheckError, match="ambiguous"):
        _load_dns_identity_from_config(config_path, "titan")


def test_load_dns_identity_rejects_malformed_json(tmp_path):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text("not json")
    with pytest.raises(FleetConnectivityCheckError, match="cannot read config"):
        _load_dns_identity_from_config(config_path, "titan")


def test_load_dns_identity_rejects_missing_targets_key(tmp_path):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text(json.dumps({}))
    with pytest.raises(FleetConnectivityCheckError, match="no 'targets' list"):
        _load_dns_identity_from_config(config_path, "titan")


# ── the example templates actually shipped in the repo ──────────────


def test_mac_coordinator_example_placeholder_is_rejected():
    example_path = REPO_ROOT / "deploy" / "mac" / "mac-coordinator.json.example"
    with pytest.raises(FleetConnectivityCheckError, match="placeholder"):
        _load_dns_identity_from_config(example_path, "titan")


# ── CLI entry point ───────────────────────────────────────────────────


def test_main_returns_zero_when_verified(monkeypatch, capsys):
    import fleet_connectivity_check as module

    monkeypatch.setattr(module, "_run_tailscale_status", lambda: ONLINE_PEER_STATUS)
    exit_code = main(["--dns-identity", "hydra-titan.example.ts.net", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verified"] is True


def test_main_returns_one_when_not_verified(monkeypatch, capsys):
    import fleet_connectivity_check as module

    monkeypatch.setattr(module, "_run_tailscale_status", lambda: ONLINE_PEER_STATUS)
    exit_code = main(["--dns-identity", "nonexistent.example.ts.net"])
    assert exit_code == 1


def test_main_returns_two_on_config_error(tmp_path, capsys):
    config_path = tmp_path / "coordinator.json"
    config_path.write_text(json.dumps({"targets": []}))
    exit_code = main(["--node", "titan", "--config", str(config_path), "--json"])
    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "config_error"


def test_main_requires_config_with_node():
    with pytest.raises(SystemExit):
        main(["--node", "titan"])
