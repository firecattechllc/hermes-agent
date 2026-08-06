from __future__ import annotations

from types import SimpleNamespace

from hermes_docs_worker.collectors import hermes_runtime
from hermes_docs_worker.status import StatusValue


def _stub_systemctl(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_docs_worker.collectors.systemd_state.run_argv",
        lambda argv, **kw: SimpleNamespace(
            returncode=0, stdout="LoadState=loaded\nActiveState=active\nSubState=running\n", stderr="",
        ),
    )


def test_source_checkout_configured_when_present(worker_config, monkeypatch) -> None:
    _stub_systemctl(monkeypatch)
    facts = {f.label: f for f in hermes_runtime.collect(worker_config, now=0)}
    assert facts["source_checkout"].status == StatusValue.CONFIGURED


def test_source_checkout_blocked_when_missing(worker_config, tmp_path, monkeypatch) -> None:
    _stub_systemctl(monkeypatch)
    object.__setattr__(worker_config, "hermes_source_dir", tmp_path / "does-not-exist")
    facts = {f.label: f for f in hermes_runtime.collect(worker_config, now=0)}
    assert facts["source_checkout"].status == StatusValue.BLOCKED


def test_only_hermes_marked_units_are_queried(worker_config, monkeypatch) -> None:
    _stub_systemctl(monkeypatch)
    object.__setattr__(
        worker_config, "systemd_allowlist", ("hermes-docs-evidence.service", "ollama.service")
    )
    facts = hermes_runtime.collect(worker_config, now=0)
    labels = {f.label for f in facts}
    assert "service:hermes-docs-evidence.service" in labels
    assert "service:ollama.service" not in labels
