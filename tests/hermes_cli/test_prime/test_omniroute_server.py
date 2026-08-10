from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.omniroute_config import TitanRoutingConfig
from hermes_cli.prime.omniroute_server import (
    BudgetTracker,
    OmniRouteServerConfigError,
    _validate_bind_host,
    build_omniroute_http_server,
)
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker, UpstreamOutcome

AUTH_TOKEN = "a" * 20


class FakeUpstream:
    def __init__(self, provider_id, *, outcomes=None):
        self.provider_id = provider_id
        self._outcomes = list(outcomes or [])
        self.circuit_breaker = CircuitBreaker()
        self.calls = []

    def generate(self, *, model, input_text, timeout_seconds):
        self.calls.append((model, input_text))
        if self._outcomes:
            return self._outcomes.pop(0)
        return UpstreamOutcome(
            succeeded=True,
            output_text=f"echo:{input_text}",
            model_used=model,
            latency_ms=1,
        )


def _config(**overrides) -> TitanRoutingConfig:
    env = {
        "HERMES_OMNIROUTE_AUTH_TOKEN": AUTH_TOKEN,
        "HERMES_OMNIROUTE_BIND_PORT": "0",
        "HERMES_OMNIROUTE_ALLOWED_MODEL_ALIASES": "embedding,lightweight,large",
        "HERMES_OMNIROUTE_ALIAS_ROUTES": (
            "embedding=titan_ollama@embeddinggemma:latest,"
            "lightweight=titan_ollama@hermes-llama3.2:3b-64k,"
            "large=freellmapi@gpt-4o-mini"
        ),
    }
    env.update(overrides)
    return TitanRoutingConfig.from_env(env)


class RunningServer:
    def __init__(self, config, upstreams, evidence_store=None, **kwargs):
        self.evidence_store = evidence_store
        self.server = build_omniroute_http_server(
            config=config, upstreams=upstreams, evidence_store=evidence_store, **kwargs
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.15)

    def call(self, path, method="GET", body=None, auth=True, correlation_id=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)


@pytest.fixture
def evidence_store(tmp_path):
    return PrimeEvidenceStore(state_root=tmp_path / "prime")


# ── local Titan Ollama routing ──────────────────────────────────────────────


def test_local_titan_ollama_routing(evidence_store) -> None:
    ollama = FakeUpstream("titan_ollama")
    server = RunningServer(_config(), {"titan_ollama": ollama}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200
        assert payload["choices"][0]["message"]["content"] == "echo:hi"
        assert ollama.calls == [("hermes-llama3.2:3b-64k", "hi")]
    finally:
        server.stop()


# ── FreeLLMAPI routing through OmniRoute ────────────────────────────────────


def test_freellmapi_routing(evidence_store) -> None:
    freellmapi = FakeUpstream("freellmapi")
    server = RunningServer(_config(), {"freellmapi": freellmapi}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "large", "messages": [{"role": "user", "content": "big task"}]},
        )
        assert status == 200
        assert payload["choices"][0]["message"]["content"] == "echo:big task"
    finally:
        server.stop()


# ── rejected unapproved / unknown provider ──────────────────────────────────


def test_rejects_unknown_alias_and_records_evidence(evidence_store) -> None:
    server = RunningServer(_config(), {}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {
                "model": "not-a-governed-alias",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert status == 403
        assert payload["reason"] == "unknown_alias"
        entries = evidence_store.read_all()
        assert len(entries) == 1
        assert entries[0]["record"]["kind"] == "omniroute_route_decision"
    finally:
        server.stop()


def test_rejects_denied_provider(evidence_store) -> None:
    config = _config(
        HERMES_OMNIROUTE_DENIED_PROVIDERS="freellmapi",
        HERMES_OMNIROUTE_PROVIDER_PRIORITY="titan_ollama",
    )
    server = RunningServer(config, {}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "large", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 403
        assert payload["reason"] == "provider_denied"
    finally:
        server.stop()


# ── offline-local-only mode ──────────────────────────────────────────────────


def test_offline_local_only_mode_blocks_remote_and_allows_local(evidence_store) -> None:
    config = _config(HERMES_OMNIROUTE_OFFLINE_LOCAL_ONLY="true")
    ollama = FakeUpstream("titan_ollama")
    server = RunningServer(config, {"titan_ollama": ollama}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "large", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 403
        assert payload["reason"] == "offline_local_only_blocks_remote"

        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200
    finally:
        server.stop()


# ── FreeLLMAPI outage with Titan Ollama still operational ──────────────────


def test_freellmapi_outage_does_not_affect_titan_ollama_routing(evidence_store) -> None:
    ollama = FakeUpstream("titan_ollama")
    freellmapi = FakeUpstream(
        "freellmapi",
        outcomes=[
            UpstreamOutcome(
                succeeded=False,
                error="upstream unreachable or timed out",
                retryable=True,
                latency_ms=3,
            )
        ],
    )
    server = RunningServer(
        _config(), {"titan_ollama": ollama, "freellmapi": freellmapi}, evidence_store
    )
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "large", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status in (502, 504)

        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {
                "model": "lightweight",
                "messages": [{"role": "user", "content": "still works"}],
            },
        )
        assert status == 200
        assert payload["choices"][0]["message"]["content"] == "echo:still works"

        health_status, health_payload = server.call("/healthz", auth=False)
        assert health_status == 200
    finally:
        server.stop()


# ── health endpoint distinguishes components ────────────────────────────────


def test_healthz_reports_component_breakdown_without_auth(evidence_store) -> None:
    ollama = FakeUpstream("titan_ollama")
    server = RunningServer(_config(), {"titan_ollama": ollama}, evidence_store)
    try:
        status, payload = server.call("/healthz", auth=False)
        assert status == 200
        for key in (
            "hermes_router",
            "omniroute",
            "titan_ollama",
            "freellmapi",
            "operational_status",
        ):
            assert key in payload
    finally:
        server.stop()


# ── secret redaction ─────────────────────────────────────────────────────────


def test_unauthorized_request_rejected_and_no_secret_leaked_in_response(
    evidence_store,
) -> None:
    server = RunningServer(_config(), {}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": [{"role": "user", "content": "hi"}]},
            auth=False,
        )
        assert status == 401
        assert AUTH_TOKEN not in json.dumps(payload)
    finally:
        server.stop()


def test_upstream_error_detail_never_contains_auth_token(evidence_store) -> None:
    ollama = FakeUpstream(
        "titan_ollama",
        outcomes=[
            UpstreamOutcome(
                succeeded=False,
                error="unreachable or timed out",
                retryable=True,
                latency_ms=2,
            )
        ],
    )
    server = RunningServer(_config(), {"titan_ollama": ollama}, evidence_store)
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status in (502, 504)
        assert AUTH_TOKEN not in json.dumps(payload)
        for entry in evidence_store.read_all():
            assert AUTH_TOKEN not in json.dumps(entry)
    finally:
        server.stop()


# ── evidence emission ────────────────────────────────────────────────────────


def test_successful_route_emits_evidence_with_required_fields(evidence_store) -> None:
    ollama = FakeUpstream("titan_ollama")
    server = RunningServer(_config(), {"titan_ollama": ollama}, evidence_store)
    try:
        server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": [{"role": "user", "content": "hi"}]},
            correlation_id="corr-xyz",
        )
        entries = evidence_store.read_all()
        assert len(entries) == 1
        summary = entries[0]["record"]["redacted_summary"]
        assert "capability=lightweight" in summary
        assert "status=succeeded" in summary
        assert entries[0]["record"]["correlation_id"] == "corr-xyz"
    finally:
        server.stop()


# ── bounded request-body size (defense in depth) ────────────────────────────


def test_provider_not_configured_returns_503_and_records_evidence(
    evidence_store,
) -> None:
    server = RunningServer(
        _config(), {}, evidence_store
    )  # no upstream registered at all
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 503
        assert payload["error"] == "provider_unavailable"
    finally:
        server.stop()


def test_missing_model_field_rejected() -> None:
    server = RunningServer(_config(), {})
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 422
    finally:
        server.stop()


def test_missing_user_message_rejected() -> None:
    ollama = FakeUpstream("titan_ollama")
    server = RunningServer(_config(), {"titan_ollama": ollama})
    try:
        status, payload = server.call(
            "/v1/chat/completions",
            "POST",
            {"model": "lightweight", "messages": []},
        )
        assert status == 422
    finally:
        server.stop()


def test_unknown_path_returns_404_when_authorized() -> None:
    server = RunningServer(_config(), {})
    try:
        status, _ = server.call("/v1/not-a-real-endpoint", auth=True)
        assert status == 404
    finally:
        server.stop()


def test_unknown_path_without_auth_returns_401_not_404() -> None:
    # Authorization is checked before routing (except /healthz) so an
    # unauthenticated caller cannot use response codes to enumerate which
    # paths exist.
    server = RunningServer(_config(), {})
    try:
        status, _ = server.call("/v1/not-a-real-endpoint", auth=False)
        assert status == 401
    finally:
        server.stop()


# ── startup configuration validation ────────────────────────────────────────


def test_validate_bind_host_rejects_wildcard() -> None:
    with pytest.raises(OmniRouteServerConfigError):
        _validate_bind_host("0.0.0.0")


def test_validate_bind_host_rejects_blank() -> None:
    with pytest.raises(OmniRouteServerConfigError):
        _validate_bind_host("")


def test_validate_bind_host_accepts_loopback() -> None:
    assert _validate_bind_host("127.0.0.1") == "127.0.0.1"


# ── budget rejection (bounded, generic mechanism) ───────────────────────────


def test_budget_tracker_rejects_when_request_budget_exceeded() -> None:
    tracker = BudgetTracker()
    assert (
        tracker.check_and_reserve(
            estimated_cost_micros=100,
            request_budget_micros=50,
            daily_budget_micros=None,
            now=0,
        )
        is False
    )


def test_budget_tracker_rejects_when_daily_budget_exhausted() -> None:
    tracker = BudgetTracker()
    assert (
        tracker.check_and_reserve(
            estimated_cost_micros=60,
            request_budget_micros=None,
            daily_budget_micros=100,
            now=0,
        )
        is True
    )
    assert (
        tracker.check_and_reserve(
            estimated_cost_micros=60,
            request_budget_micros=None,
            daily_budget_micros=100,
            now=0,
        )
        is False
    )


def test_budget_tracker_resets_daily_counter_on_new_day() -> None:
    tracker = BudgetTracker()
    assert (
        tracker.check_and_reserve(
            estimated_cost_micros=90,
            request_budget_micros=None,
            daily_budget_micros=100,
            now=0,
        )
        is True
    )
    next_day = 86_400
    assert (
        tracker.check_and_reserve(
            estimated_cost_micros=90,
            request_budget_micros=None,
            daily_budget_micros=100,
            now=next_day,
        )
        is True
    )
