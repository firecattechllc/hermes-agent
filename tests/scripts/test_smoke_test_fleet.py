"""Tests for the non-destructive fleet activation smoke test.

Every check in scripts/smoke_test_fleet.py is read-only. These tests stub
out subprocess calls and environment state so they run deterministically
without needing a real `hermes` install, real credentials, or a live
tailnet.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import smoke_test_fleet as module  # noqa: E402
from smoke_test_fleet import (  # noqa: E402
    CheckOutcome,
    _present_in_hermes_env,
    check_capability_manifest,
    check_credential_presence,
    check_deploy_templates_present,
    main,
)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["hermes"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── capability manifest check ────────────────────────────────────────


def test_capability_manifest_check_passes_against_real_manifest():
    result = check_capability_manifest()
    assert result.outcome == CheckOutcome.PASS
    assert "entries" in result.detail


def test_capability_manifest_check_fails_closed_on_load_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("manifest missing")

    monkeypatch.setattr("hermes_cli.capability_manifest.load_capability_manifest", _boom)
    result = module.check_capability_manifest()
    assert result.outcome == CheckOutcome.FAIL
    assert "manifest missing" in result.detail


# ── credential presence ──────────────────────────────────────────────


def test_credential_presence_skip_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_LINK_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    results = check_credential_presence()
    assert all(r.outcome == CheckOutcome.SKIP for r in results)


def test_credential_presence_pass_when_env_set(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-value")
    monkeypatch.delenv("HERMES_LINK_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    results = {r.name: r for r in check_credential_presence()}
    assert results["credential_present:TELEGRAM_BOT_TOKEN"].outcome == CheckOutcome.PASS
    assert results["credential_present:HERMES_LINK_TOKEN"].outcome == CheckOutcome.SKIP


def test_credential_presence_never_leaks_value(monkeypatch, tmp_path):
    secret_value = "super-secret-do-not-print-12345"
    monkeypatch.setenv("GITHUB_TOKEN", secret_value)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    results = check_credential_presence()
    for result in results:
        assert secret_value not in result.detail
        assert secret_value not in result.name


def test_present_in_hermes_env_reads_var_names_only(monkeypatch, tmp_path):
    env_file = tmp_path / ".hermes" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nHERMES_LINK_TOKEN=\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _present_in_hermes_env("TELEGRAM_BOT_TOKEN") is True
    assert _present_in_hermes_env("HERMES_LINK_TOKEN") is False
    assert _present_in_hermes_env("GITHUB_TOKEN") is False


# ── deploy templates present ─────────────────────────────────────────


def test_deploy_templates_present_passes_in_repo():
    result = check_deploy_templates_present()
    assert result.outcome == CheckOutcome.PASS


def test_deploy_templates_present_fails_closed_when_missing(monkeypatch):
    monkeypatch.setattr(module, "REPO_ROOT", Path("/nonexistent/path/for/testing"))
    result = check_deploy_templates_present()
    assert result.outcome == CheckOutcome.FAIL
    assert "missing" in result.detail


# ── subprocess-backed checks: never touch the real `hermes` process ──


def test_hermes_doctor_pass_on_zero_exit(monkeypatch):
    monkeypatch.setattr(module, "_run_hermes", lambda *a: _completed(returncode=0))
    result = module.check_hermes_doctor()
    assert result.outcome == CheckOutcome.PASS


def test_hermes_doctor_warns_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(module, "_run_hermes", lambda *a: _completed(returncode=1))
    result = module.check_hermes_doctor()
    assert result.outcome == CheckOutcome.WARN


def test_hermes_doctor_fails_closed_when_binary_missing(monkeypatch):
    def _raise(*a):
        raise FileNotFoundError("hermes not found")

    monkeypatch.setattr(module, "_run_hermes", _raise)
    result = module.check_hermes_doctor()
    assert result.outcome == CheckOutcome.FAIL


def test_cron_status_pass_when_gateway_running(monkeypatch):
    monkeypatch.setattr(
        module, "_run_hermes", lambda *a: _completed(returncode=0, stdout="Gateway is running")
    )
    result = module.check_cron_status()
    assert result.outcome == CheckOutcome.PASS


def test_cron_status_warns_when_gateway_not_running(monkeypatch):
    monkeypatch.setattr(
        module, "_run_hermes",
        lambda *a: _completed(returncode=0, stdout="Gateway is not running — cron jobs will NOT fire"),
    )
    result = module.check_cron_status()
    assert result.outcome == CheckOutcome.WARN


def test_cron_status_fails_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(module, "_run_hermes", lambda *a: _completed(returncode=1, stderr="boom"))
    result = module.check_cron_status()
    assert result.outcome == CheckOutcome.FAIL


def test_hermes_link_status_pass_when_reachable(monkeypatch):
    monkeypatch.setattr(module, "_run_hermes", lambda *a: _completed(returncode=0))
    result = module.check_hermes_link_status()
    assert result.outcome == CheckOutcome.PASS


def test_hermes_link_status_skips_when_token_missing(monkeypatch):
    monkeypatch.setattr(
        module, "_run_hermes",
        lambda *a: _completed(returncode=1, stderr="link: HERMES_LINK_TOKEN is not configured"),
    )
    result = module.check_hermes_link_status()
    assert result.outcome == CheckOutcome.SKIP


def test_hermes_link_status_warns_on_other_failure(monkeypatch):
    monkeypatch.setattr(
        module, "_run_hermes", lambda *a: _completed(returncode=1, stderr="connection refused")
    )
    result = module.check_hermes_link_status()
    assert result.outcome == CheckOutcome.WARN


def test_computer_use_doctor_skips_on_non_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    result = module.check_computer_use_doctor()
    assert result.outcome == CheckOutcome.SKIP


# ── tailscale integration ────────────────────────────────────────────


def test_tailscale_node_check_skips_without_identity():
    result = module.check_tailscale_node(None, "titan")
    assert result.outcome == CheckOutcome.SKIP


def test_tailscale_node_check_delegates_to_connectivity_module(monkeypatch):
    # check_tailscale_node imports `scripts.fleet_connectivity_check` (the
    # namespace-package path) internally, which is a distinct module cache
    # entry from the bare `fleet_connectivity_check` imported at the top of
    # this file via the sys.path.insert convention — patch the same one
    # the function under test actually resolves.
    import scripts.fleet_connectivity_check as scoped_module

    fake_result = scoped_module.NodeConnectivityResult(
        node="hydra-titan.example.ts.net", tailscale_running=True, peer_found=True,
        peer_online=True, peer_hostname="hydra-titan", verified=True, reason="ok",
    )
    monkeypatch.setattr(
        scoped_module, "check_node_connectivity", lambda *a, **k: fake_result
    )
    result = module.check_tailscale_node("hydra-titan.example.ts.net", "titan")
    assert result.outcome == CheckOutcome.PASS


# ── CLI entry point / exit codes ─────────────────────────────────────


def test_main_returns_zero_when_nothing_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        module, "run_all_checks",
        lambda **k: [module.CheckResult("fake_check", CheckOutcome.PASS, "ok")],
    )
    exit_code = main([])
    assert exit_code == 0
    assert "1 checks" in capsys.readouterr().out


def test_main_returns_one_when_any_check_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        module, "run_all_checks",
        lambda **k: [
            module.CheckResult("fake_check", CheckOutcome.PASS, "ok"),
            module.CheckResult("broken_check", CheckOutcome.FAIL, "boom"),
        ],
    )
    exit_code = main([])
    assert exit_code == 1


def test_main_json_output_is_valid_json(monkeypatch, capsys):
    import json

    monkeypatch.setattr(
        module, "run_all_checks",
        lambda **k: [module.CheckResult("fake_check", CheckOutcome.PASS, "ok")],
    )
    main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"name": "fake_check", "outcome": "pass", "detail": "ok"}]
