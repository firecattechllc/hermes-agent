from __future__ import annotations

import os

from hermes_docs_worker.cli import main


def _set_env(monkeypatch, worker_config) -> None:
    monkeypatch.setenv("HERMES_DOCS_WORKER_HERMES_SOURCE_DIR", str(worker_config.hermes_source_dir))
    monkeypatch.setenv("HERMES_DOCS_WORKER_DOCS_REPO_PATH", str(worker_config.docs_repo_path))
    monkeypatch.setenv("HERMES_DOCS_WORKER_STATE_DIR", str(worker_config.state_dir))
    monkeypatch.setenv("HERMES_DOCS_WORKER_GITHUB_REPO", worker_config.github_repo)
    monkeypatch.setenv("HERMES_DOCS_WORKER_SYSTEMD_ALLOWLIST", ",".join(worker_config.systemd_allowlist))
    monkeypatch.setenv("HERMES_DOCS_WORKER_MIN_PR_INTERVAL_SECONDS", "0")


def test_validate_config_ok(worker_config, monkeypatch, capsys) -> None:
    _set_env(monkeypatch, worker_config)
    assert main(["validate-config"]) == 0
    assert "OK" in capsys.readouterr().out


def test_validate_config_missing_required(monkeypatch, capsys) -> None:
    for key in list(os.environ):
        if key.startswith("HERMES_DOCS_WORKER_"):
            monkeypatch.delenv(key, raising=False)
    assert main(["validate-config"]) == 2
    assert "invalid" in capsys.readouterr().err


def test_status_reports_no_prior_run(worker_config, monkeypatch, capsys) -> None:
    _set_env(monkeypatch, worker_config)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "last evidence run: none recorded" in out
    assert str(worker_config.docs_repo_path) in out


def test_collect_dry_run_via_cli(worker_config, monkeypatch, capsys) -> None:
    _set_env(monkeypatch, worker_config)
    monkeypatch.setattr("hermes_docs_worker.collectors.systemd_state.run_argv", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    # collect --dry-run must exit 0 even when a collector fails, and must
    # never require --dry-run's caller to also pass a working Ollama, gh,
    # or systemctl.
    exit_code = main(["collect", "--dry-run"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
