from __future__ import annotations

import pytest

from hermes_cli.prime.titan_idle_manager import IdleManagerConfigError, IdleModelManager


class FakeUnloadTransport:
    def __init__(self, *, raises: Exception | None = None):
        self._raises = raises
        self.calls = []

    def post(self, url, payload, *, timeout_seconds):
        self.calls.append((url, payload))
        if self._raises is not None:
            raise self._raises
        return {}


def test_touch_then_sweep_before_threshold_does_not_unload(tmp_path) -> None:
    transport = FakeUnloadTransport()
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=600,
        transport=transport,
    )
    manager.touch("lightweight-model", now=1_000)
    outcomes = manager.sweep(now=1_100)
    assert outcomes == ()
    assert transport.calls == []


def test_sweep_unloads_model_idle_past_threshold(tmp_path) -> None:
    transport = FakeUnloadTransport()
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
        transport=transport,
    )
    manager.touch("lightweight-model", now=1_000)
    outcomes = manager.sweep(now=1_100)
    assert len(outcomes) == 1
    assert outcomes[0].succeeded is True
    assert transport.calls == [
        (
            "http://127.0.0.1:11434/api/generate",
            {"model": "lightweight-model", "keep_alive": 0},
        )
    ]


def test_unloaded_model_is_no_longer_tracked(tmp_path) -> None:
    transport = FakeUnloadTransport()
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
        transport=transport,
    )
    manager.touch("m1", now=1_000)
    manager.sweep(now=1_100)
    assert manager.last_used_models() == {}


def test_touch_after_unload_reloads_tracking(tmp_path) -> None:
    transport = FakeUnloadTransport()
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
        transport=transport,
    )
    manager.touch("m1", now=1_000)
    manager.sweep(now=1_100)
    manager.touch("m1", now=1_200)
    assert manager.last_used_models() == {"m1": 1_200}


def test_sweep_failure_does_not_crash_and_keeps_tracking(tmp_path) -> None:
    transport = FakeUnloadTransport(raises=RuntimeError("connection refused"))
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
        transport=transport,
    )
    manager.touch("m1", now=1_000)
    outcomes = manager.sweep(now=1_100)
    assert len(outcomes) == 1
    assert outcomes[0].succeeded is False
    assert outcomes[0].attempted is True
    # attempted=True means it's still dropped from tracking even though it
    # failed -- avoids retry-storming an unreachable Ollama endpoint forever.
    assert manager.last_used_models() == {}


def test_sweep_with_no_transport_configured_reports_not_attempted(tmp_path) -> None:
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
    )
    manager.touch("m1", now=1_000)
    outcomes = manager.sweep(now=1_100)
    assert outcomes[0].attempted is False
    assert manager.last_used_models() == {
        "m1": 1_000
    }  # still tracked, nothing was attempted


def test_multiple_models_only_idle_ones_unloaded(tmp_path) -> None:
    transport = FakeUnloadTransport()
    manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
        transport=transport,
    )
    manager.touch("old-model", now=1_000)
    manager.touch("fresh-model", now=1_090)
    outcomes = manager.sweep(now=1_100)
    assert {o.model for o in outcomes} == {"old-model"}
    assert manager.last_used_models() == {"fresh-model": 1_090}


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(idle_threshold_seconds=0),
        dict(idle_threshold_seconds=-5),
    ],
)
def test_rejects_invalid_construction(tmp_path, kwargs) -> None:
    defaults = dict(
        state_path=tmp_path / "idle.json", ollama_endpoint="http://127.0.0.1:11434"
    )
    defaults.update(kwargs)
    with pytest.raises(IdleManagerConfigError):
        IdleModelManager(**defaults)


def test_rejects_relative_state_path() -> None:
    with pytest.raises(IdleManagerConfigError):
        IdleModelManager(
            state_path="relative/idle.json", ollama_endpoint="http://127.0.0.1:11434"
        )  # type: ignore[arg-type]


def test_state_persists_across_manager_instances(tmp_path) -> None:
    path = tmp_path / "idle.json"
    manager1 = IdleModelManager(
        state_path=path, ollama_endpoint="http://127.0.0.1:11434"
    )
    manager1.touch("m1", now=1_000)

    manager2 = IdleModelManager(
        state_path=path, ollama_endpoint="http://127.0.0.1:11434"
    )
    assert manager2.last_used_models() == {"m1": 1_000}
