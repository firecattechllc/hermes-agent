from __future__ import annotations

from sigil.ai.goose import GooseWorkerConfig, GooseWorkerProvider, GooseProcessResult
from sigil.desktop_bridge.goose_bridge import goose_worker_visibility


class _FakeRunner:
    def run(self, args, *, cwd, env, timeout_seconds, cancel_event=None):
        del cwd, env, timeout_seconds, cancel_event
        if len(args) >= 2 and args[1] == "--version":
            return GooseProcessResult(returncode=0, stdout=b"goose 1.45.0", stderr=b"")
        return GooseProcessResult(returncode=0, stdout=b"", stderr=b"")


def test_visibility_degrades_gracefully_when_disabled(tmp_path):
    result = goose_worker_visibility(GooseWorkerConfig(executable=str(tmp_path / "missing")))
    assert result["available"] is True
    assert result["enabled"] is False
    assert result["installed"] is False
    assert result["active_jobs"] == 0
    assert result["last_execution"] is None


def test_visibility_never_raises_on_probe_failure(monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "sigil.desktop_bridge.goose_bridge.GooseInspector",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = goose_worker_visibility(GooseWorkerConfig())
    assert result["available"] is False
    assert result["health"] == "unavailable"


def test_visibility_reflects_active_jobs_and_last_execution(tmp_path):
    script = tmp_path / "goose"
    script.write_text("#!/usr/bin/env python3\nprint('goose 1.45.0')\n")
    script.chmod(0o755)
    config = GooseWorkerConfig(enabled=True, executable=str(script))
    provider = GooseWorkerProvider(config, runner=_FakeRunner())
    result = goose_worker_visibility(config, provider)
    assert result["installed"] is True
    assert result["active_jobs"] == 0
    assert result["last_execution"] is None
