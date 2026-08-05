from __future__ import annotations

import threading
import time

import pytest

from hermes_cli.prime.client import PrimeClientError
from hermes_cli.prime.entrypoints import (
    PrimeEntrypointConfigError,
    PrimeServiceConfig,
    WorkerServiceConfig,
    _default_health_probe,
    run_prime_service,
    run_worker_service,
)
from hermes_cli.prime.fleet_registry import FleetNodeRole
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatOutcome, HeartbeatSubmission


def _now() -> int:
    return int(time.time())


# ── PrimeServiceConfig ───────────────────────────────────────────────────────

def test_prime_service_config_requires_state_root(tmp_path) -> None:
    env = {
        "HERMES_PRIME_BIND_HOST": "100.64.0.1",
        "HERMES_PRIME_AUTH_TOKEN": "a" * 20,
    }
    with pytest.raises(PrimeEntrypointConfigError):
        PrimeServiceConfig.from_env(env)


def test_prime_service_config_rejects_relative_state_root() -> None:
    env = {
        "HERMES_PRIME_STATE_ROOT": "relative/path",
        "HERMES_PRIME_BIND_HOST": "100.64.0.1",
        "HERMES_PRIME_AUTH_TOKEN": "a" * 20,
    }
    with pytest.raises(PrimeEntrypointConfigError):
        PrimeServiceConfig.from_env(env)


def test_prime_service_config_parses_from_environment(tmp_path) -> None:
    env = {
        "HERMES_PRIME_STATE_ROOT": str(tmp_path / "prime"),
        "HERMES_PRIME_BIND_HOST": "100.64.0.1",
        "HERMES_PRIME_BIND_PORT": "9000",
        "HERMES_PRIME_AUTH_TOKEN": "a" * 20,
        "HERMES_PRIME_PROJECT_ID": "my-fleet",
    }
    config = PrimeServiceConfig.from_env(env)
    assert config.bind_port == 9000
    assert config.project_id == "my-fleet"


# ── WorkerServiceConfig ──────────────────────────────────────────────────────

def test_worker_service_config_requires_endpoint() -> None:
    env = {
        "HERMES_PRIME_BASE_URL": "http://prime.tailnet.internal:8743",
        "HERMES_PRIME_AUTH_TOKEN": "a" * 20,
    }
    with pytest.raises(PrimeEntrypointConfigError):
        WorkerServiceConfig.from_env(natural_key="titan", role=FleetNodeRole.TITAN, env=env)


def test_worker_service_config_parses_capabilities() -> None:
    env = {
        "HERMES_PRIME_BASE_URL": "http://prime.tailnet.internal:8743",
        "HERMES_PRIME_AUTH_TOKEN": "a" * 20,
        "HERMES_TITAN_ENDPOINT": "http://titan.tailnet.internal:11434",
        "HERMES_TITAN_CAPABILITIES": "worker_heartbeat, local_model_inference ,",
    }
    config = WorkerServiceConfig.from_env(natural_key="titan", role=FleetNodeRole.TITAN, env=env)
    assert config.declared_capabilities == ("worker_heartbeat", "local_model_inference")
    assert config.heartbeat_interval_seconds == 30


# ── run_prime_service ────────────────────────────────────────────────────────

def test_run_prime_service_starts_and_stops_cleanly(tmp_path) -> None:
    config = PrimeServiceConfig(
        state_root=tmp_path / "prime",
        bind_host="127.0.0.1",
        bind_port=0,
        auth_token="a" * 20,
        project_id="test-fleet",
        integrity_check_interval_seconds=3600,
    )
    stop_event = threading.Event()
    service_thread = threading.Thread(
        target=run_prime_service, kwargs={"config": config, "stop_event": stop_event}
    )
    service_thread.start()
    time.sleep(0.3)  # let the HTTP server bind
    stop_event.set()
    service_thread.join(timeout=5)
    assert not service_thread.is_alive()


# ── run_worker_service ───────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, *, fail_register: bool = False) -> None:
        self.registered = []
        self.heartbeats = []
        self._fail_register = fail_register

    def register_node(self, request):
        if self._fail_register:
            raise PrimeClientError("simulated registration failure")
        self.registered.append(request)
        return {"outcome": "registered"}

    def heartbeat(self, submission):
        self.heartbeats.append(submission)
        return {"outcome": "accepted", "connection_state": "connected"}


def _worker_config(**overrides) -> WorkerServiceConfig:
    fields = dict(
        natural_key="titan", role=FleetNodeRole.TITAN,
        prime_base_url="http://prime.tailnet.internal:8743", auth_token="a" * 20,
        endpoint="http://titan.tailnet.internal:11434", software_version="1.0.0",
        protocol_version=1, declared_capabilities=("worker_heartbeat",),
        heartbeat_interval_seconds=0, ollama_endpoint=None, ollama_timeout_ms=5000,
    )
    fields.update(overrides)
    return WorkerServiceConfig(**fields)


def test_worker_service_registers_then_heartbeats_until_stopped() -> None:
    client = FakeClient()
    stop_event = threading.Event()

    def probe():
        return HeartbeatSubmission(
            natural_key="titan", liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=_now(),
        )

    def stop_after_a_few_beats():
        while len(client.heartbeats) < 3:
            time.sleep(0.01)
        stop_event.set()

    stopper = threading.Thread(target=stop_after_a_few_beats)
    stopper.start()
    run_worker_service(_worker_config(), stop_event=stop_event, health_probe=probe, client=client)
    stopper.join(timeout=5)

    assert len(client.registered) == 1
    assert len(client.heartbeats) >= 3


def test_worker_service_survives_registration_failure_and_still_heartbeats() -> None:
    client = FakeClient(fail_register=True)
    stop_event = threading.Event()

    def probe():
        return HeartbeatSubmission(
            natural_key="titan", liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=_now(),
        )

    def stop_after_first_beat():
        while len(client.heartbeats) < 1:
            time.sleep(0.01)
        stop_event.set()

    stopper = threading.Thread(target=stop_after_first_beat)
    stopper.start()
    run_worker_service(_worker_config(), stop_event=stop_event, health_probe=probe, client=client)
    stopper.join(timeout=5)

    assert client.registered == []
    assert len(client.heartbeats) >= 1


# ── default health probe ─────────────────────────────────────────────────────

def test_default_health_probe_without_ollama_reports_alive_ready() -> None:
    probe = _default_health_probe(_worker_config(ollama_endpoint=None))
    submission = probe()
    assert submission.liveness == LivenessState.ALIVE
    assert submission.readiness == ReadinessState.READY
    assert submission.reported_model_inventory == ()
