from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_docs_worker.collectors import systemd_state
from hermes_docs_worker.status import StatusValue


def _patch_run_argv(monkeypatch, stdout: str, returncode: int = 0) -> None:
    monkeypatch.setattr(
        "hermes_docs_worker.collectors.systemd_state.run_argv",
        lambda argv, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=""),
    )


def test_collect_unit_rejects_unit_outside_allowlist(worker_config) -> None:
    with pytest.raises(ValueError):
        systemd_state.collect_unit(worker_config, "not-allowlisted.service", now=0)


def test_active_running_is_deployed(worker_config, monkeypatch) -> None:
    _patch_run_argv(monkeypatch, "LoadState=loaded\nActiveState=active\nSubState=running\n")
    fact = systemd_state.collect_unit(worker_config, "hermes-docs-evidence.service", now=0)
    assert fact.status == StatusValue.DEPLOYED


def test_failed_is_blocked(worker_config, monkeypatch) -> None:
    _patch_run_argv(monkeypatch, "LoadState=loaded\nActiveState=failed\nSubState=failed\n")
    fact = systemd_state.collect_unit(worker_config, "hermes-docs-evidence.service", now=0)
    assert fact.status == StatusValue.BLOCKED


def test_not_found_is_unknown(worker_config, monkeypatch) -> None:
    _patch_run_argv(monkeypatch, "LoadState=not-found\nActiveState=inactive\nSubState=dead\n")
    fact = systemd_state.collect_unit(worker_config, "hermes-docs-evidence.service", now=0)
    assert fact.status == StatusValue.UNKNOWN


def test_inactive_is_configured(worker_config, monkeypatch) -> None:
    _patch_run_argv(monkeypatch, "LoadState=loaded\nActiveState=inactive\nSubState=dead\n")
    fact = systemd_state.collect_unit(worker_config, "hermes-docs-evidence.service", now=0)
    assert fact.status == StatusValue.CONFIGURED


def test_command_failure_is_unknown_not_a_crash(worker_config, monkeypatch) -> None:
    _patch_run_argv(monkeypatch, "", returncode=1)
    fact = systemd_state.collect_unit(worker_config, "hermes-docs-evidence.service", now=0)
    assert fact.status == StatusValue.UNKNOWN


def test_collect_only_queries_allowlisted_units(worker_config, monkeypatch) -> None:
    seen = []

    def _fake(argv, **kw):
        seen.append(argv[2])
        return SimpleNamespace(returncode=0, stdout="LoadState=loaded\nActiveState=active\nSubState=running\n", stderr="")

    monkeypatch.setattr("hermes_docs_worker.collectors.systemd_state.run_argv", _fake)
    facts = systemd_state.collect(worker_config, now=0)
    assert set(seen) == set(worker_config.systemd_allowlist)
    assert len(facts) == len(worker_config.systemd_allowlist)
