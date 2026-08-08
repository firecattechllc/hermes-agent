from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.dispatch_gate import CertificationSnapshot
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission
from hermes_cli.prime.sigil_route_server import (
    NodeModelAliasConfig,
    SigilRouteConfigurationError,
    handle_sigil_route_request,
)


def _now() -> int:
    return int(time.time())


def _register_and_heartbeat(runtime: FleetRuntime, natural_key: str, role: FleetNodeRole, *, now: int) -> None:
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id=f"reg-{natural_key}",
            natural_key=natural_key,
            role=role,
            declared_capabilities=("worker_heartbeat", "local_model_inference", "desktop_use"),
            endpoint=f"http://{natural_key}.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key=natural_key, liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )


@pytest.fixture()
def runtime(tmp_path: Path) -> FleetRuntime:
    return FleetRuntime(state_root=tmp_path / "prime", project_id="sigil-route-test")


def _certified() -> CertificationSnapshot:
    return CertificationSnapshot(status=CertificationStatus.CERTIFIED, evidence_ref="evidence://test")


def test_node_model_alias_config_parses_json_env() -> None:
    config = NodeModelAliasConfig.from_env(
        {"HERMES_PRIME_NODE_MODEL_ALIASES": '{"titan": {"sentiment": "qwen3:0.6b"}}'}
    )
    assert config.for_node("titan") == {"sentiment": "qwen3:0.6b"}
    assert config.for_node("mac") == {}


def test_node_model_alias_config_empty_env_is_empty() -> None:
    assert NodeModelAliasConfig.from_env({}).aliases_by_node == {}


def test_node_model_alias_config_rejects_malformed_json() -> None:
    with pytest.raises(SigilRouteConfigurationError):
        NodeModelAliasConfig.from_env({"HERMES_PRIME_NODE_MODEL_ALIASES": "not json"})


def test_node_model_alias_config_rejects_non_string_values() -> None:
    with pytest.raises(SigilRouteConfigurationError):
        NodeModelAliasConfig.from_env(
            {"HERMES_PRIME_NODE_MODEL_ALIASES": '{"titan": {"sentiment": 5}}'}
        )


def test_route_rejects_unsupported_operation(runtime: FleetRuntime) -> None:
    result = handle_sigil_route_request(
        fleet_runtime=runtime,
        node_aliases=NodeModelAliasConfig(aliases_by_node={}),
        certification_provider=_certified,
        body={"operation": "not_a_real_operation"},
    )
    assert result["ok"] is False
    assert result["error"] == "unsupported_operation"


def test_route_fails_closed_when_caller_not_registered(runtime: FleetRuntime) -> None:
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    result = handle_sigil_route_request(
        fleet_runtime=runtime,
        node_aliases=NodeModelAliasConfig(aliases_by_node={"titan": {"sentiment": "qwen3:0.6b"}}),
        certification_provider=_certified,
        body={"operation": "advisory_financial_sentiment"},
        now=now,
    )
    # "mac" (the caller) was never registered.
    assert result["ok"] is False
    assert result["error"] == "caller_not_admitted"


def test_route_fails_closed_when_service_node_has_no_configured_alias(
    runtime: FleetRuntime,
) -> None:
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    result = handle_sigil_route_request(
        fleet_runtime=runtime,
        node_aliases=NodeModelAliasConfig(aliases_by_node={}),  # no alias configured anywhere
        certification_provider=_certified,
        body={"operation": "advisory_financial_sentiment"},
        now=now,
    )
    assert result["ok"] is False
    assert result["outcome"] == "rejected"
    # Admission/health are still satisfied here — but with no alias
    # configured, no adapter exists for "titan" at all, so
    # SigilRoutingService.route() itself reports the node as not admitted
    # for dispatch purposes rather than attempting a network call.
    assert result["rejection_code"] == "service_not_admitted"


def test_route_fails_closed_for_stale_service_node(runtime: FleetRuntime) -> None:
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    stale_now = now + 100_000  # well past DEFAULT_MAX_REPORT_AGE_SECONDS
    result = handle_sigil_route_request(
        fleet_runtime=runtime,
        node_aliases=NodeModelAliasConfig(aliases_by_node={"titan": {"sentiment": "qwen3:0.6b"}}),
        certification_provider=_certified,
        body={"operation": "advisory_financial_sentiment"},
        now=stale_now,
    )
    assert result["ok"] is False
    assert result["service_health_usable"] is False


def test_route_dispatches_through_real_governed_adapter(
    runtime: FleetRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: real admission + real health + a real (network-mocked at
    the transport boundary only) Ollama generate call through the governed
    adapter chain — proves the whole assembly wires together correctly."""
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    import hermes_cli.prime.ollama_node as ollama_node_module

    class _FakeTransport:
        def get(self, url: str, *, timeout_seconds: float):
            return {"models": [{"name": "qwen3:0.6b"}]}

        def post(self, url: str, payload: dict, *, timeout_seconds: float):
            return {"response": "governed sentiment output"}

    monkeypatch.setattr(ollama_node_module, "UrllibOllamaTransport", _FakeTransport)

    result = handle_sigil_route_request(
        fleet_runtime=runtime,
        node_aliases=NodeModelAliasConfig(aliases_by_node={"titan": {"sentiment": "qwen3:0.6b"}}),
        certification_provider=_certified,
        body={"operation": "advisory_financial_sentiment", "input_payload": {"symbol": "TEST"}},
        now=now,
    )
    assert result["ok"] is True
    assert result["outcome"] == "accepted"
    assert result["advisory_output"]["routed_to"] == "titan"
    assert result["advisory_output"]["model_alias"] == "sentiment"
    assert result["caller_admitted"] is True
    assert result["service_admitted"] is True


def test_route_never_dispatches_to_unadmitted_node_even_with_alias_configured(
    runtime: FleetRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model alias being configured is not, by itself, authorization."""
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    runtime.revoke_node("titan", now=now, reason="test-revocation")

    import hermes_cli.prime.ollama_node as ollama_node_module

    class _FakeTransport:
        def get(self, url: str, *, timeout_seconds: float):
            return {"models": [{"name": "qwen3:0.6b"}]}

        def post(self, url: str, payload: dict, *, timeout_seconds: float):
            raise AssertionError("must never reach the network for a revoked node")

    monkeypatch.setattr(ollama_node_module, "UrllibOllamaTransport", _FakeTransport)

    result = handle_sigil_route_request(
        fleet_runtime=runtime,
        node_aliases=NodeModelAliasConfig(aliases_by_node={"titan": {"sentiment": "qwen3:0.6b"}}),
        certification_provider=_certified,
        body={"operation": "advisory_financial_sentiment"},
        now=now,
    )
    assert result["ok"] is False
    assert result["service_admitted"] is False
