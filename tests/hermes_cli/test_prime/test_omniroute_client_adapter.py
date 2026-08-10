from __future__ import annotations

import hashlib
import json

import pytest

from hermes_cli.agent_roles.model_execution import (
    GovernedModelExecutionService,
    InMemoryModelExecutionStore,
    ModelExecutionErrorClass,
    ModelExecutionRequest,
    ModelExecutionState,
)
from hermes_cli.agent_roles.model_routing import (
    CandidateDisposition,
    CandidateScore,
    RoutingDecision,
    RoutingPolicyOutcome,
)
from hermes_cli.prime.dispatch_gate import InMemoryReferenceStore
from hermes_cli.prime.omniroute_client_adapter import (
    OmniRouteClientTransportError,
    OmniRouteHTTPProviderAdapter,
)


class FakeTransport:
    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def post_chat_completion(self, url, payload, *, auth_token, timeout_seconds):
        self.calls.append((url, payload, auth_token))
        if self._error is not None:
            raise self._error
        return self._response


def _chat_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _adapter(provider_id, transport, *, resolver=None) -> OmniRouteHTTPProviderAdapter:
    store = InMemoryReferenceStore()
    store.put("input://req", "hello")
    return OmniRouteHTTPProviderAdapter(
        provider_id=provider_id,
        base_url="http://127.0.0.1:8791",
        auth_token="a" * 20,
        input_resolver=resolver or store.resolve,
        transport=transport,
    )


# ── unit behavior ─────────────────────────────────────────────────────────


def test_execute_success_path() -> None:
    transport = FakeTransport(response=_chat_response("hi back"))
    adapter = _adapter("omniroute_titan_ollama", transport)
    result = adapter.execute(
        model_id="lightweight", input_reference="input://req", timeout_seconds=5
    )
    assert result.error_classification is None
    assert result.output_reference is not None
    assert result.usage.actual_cost_micros == 0
    assert transport.calls[0][2] == "a" * 20  # auth token passed through, never logged


def test_execute_empty_model_id_rejected_without_network_call() -> None:
    transport = FakeTransport(response=_chat_response("unused"))
    adapter = _adapter("omniroute_titan_ollama", transport)
    result = adapter.execute(
        model_id="", input_reference="input://req", timeout_seconds=5
    )
    assert result.error_classification == ModelExecutionErrorClass.INVALID_REQUEST
    assert transport.calls == []


def test_execute_unresolvable_input_rejected() -> None:
    transport = FakeTransport(response=_chat_response("unused"))
    adapter = _adapter("omniroute_titan_ollama", transport, resolver=lambda ref: None)
    result = adapter.execute(
        model_id="lightweight", input_reference="input://missing", timeout_seconds=5
    )
    assert result.error_classification == ModelExecutionErrorClass.INVALID_REQUEST


def test_execute_timeout_classified_as_timeout() -> None:
    transport = FakeTransport(
        error=OmniRouteClientTransportError(
            "OmniRoute endpoint unreachable or timed out", retryable=True
        )
    )
    adapter = _adapter("omniroute_titan_ollama", transport)
    result = adapter.execute(
        model_id="lightweight", input_reference="input://req", timeout_seconds=5
    )
    assert result.error_classification == ModelExecutionErrorClass.TIMEOUT


def test_execute_retryable_server_error_classified_as_provider_unavailable() -> None:
    transport = FakeTransport(
        error=OmniRouteClientTransportError("HTTP 503", retryable=True)
    )
    adapter = _adapter("omniroute_titan_ollama", transport)
    result = adapter.execute(
        model_id="lightweight", input_reference="input://req", timeout_seconds=5
    )
    assert result.error_classification == ModelExecutionErrorClass.PROVIDER_UNAVAILABLE


def test_execute_non_retryable_error_classified_as_permanent() -> None:
    transport = FakeTransport(
        error=OmniRouteClientTransportError("HTTP 403", retryable=False)
    )
    adapter = _adapter("omniroute_titan_ollama", transport)
    result = adapter.execute(
        model_id="lightweight", input_reference="input://req", timeout_seconds=5
    )
    assert (
        result.error_classification == ModelExecutionErrorClass.PERMANENT_PROVIDER_ERROR
    )


def test_execute_malformed_response_fails_output_validation() -> None:
    transport = FakeTransport(response={"unexpected": "shape"})
    adapter = _adapter("omniroute_titan_ollama", transport)
    result = adapter.execute(
        model_id="lightweight", input_reference="input://req", timeout_seconds=5
    )
    assert (
        result.error_classification == ModelExecutionErrorClass.OUTPUT_VALIDATION_FAILED
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(provider_id=""),
        dict(base_url=""),
        dict(auth_token="short"),
    ],
)
def test_rejects_invalid_construction(kwargs) -> None:
    defaults = dict(
        provider_id="omniroute_titan_ollama",
        base_url="http://127.0.0.1:8791",
        auth_token="a" * 20,
        input_resolver=lambda ref: "x",
    )
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        OmniRouteHTTPProviderAdapter(**defaults)


# ── integration: reuse of the pre-existing governed execution pipeline ─────
# Proves the required "approved fallback" behavior: when the higher-priority
# omniroute_titan_ollama route fails, GovernedModelExecutionService (entirely
# unmodified) falls over to omniroute_freellmapi using its own pre-existing
# fallback logic -- this new adapter needed no special-casing to participate
# in it.


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def test_governed_execution_falls_back_from_titan_ollama_to_freellmapi() -> None:
    failing_transport = FakeTransport(
        error=OmniRouteClientTransportError("HTTP 503", retryable=True)
    )
    succeeding_transport = FakeTransport(response=_chat_response("fallback answer"))

    local_adapter = _adapter("omniroute_titan_ollama", failing_transport)
    remote_adapter = _adapter("omniroute_freellmapi", succeeding_transport)

    candidates = (
        CandidateScore(
            provider_id="omniroute_titan_ollama",
            model_id="lightweight",
            disposition=CandidateDisposition.ELIGIBLE,
            estimated_cost_micros=0,
            score=90,
            quality_factor=80,
            reliability_factor=80,
            latency_factor=90,
            cost_factor=100,
            preference_factor=100,
            trust_factor=100,
        ),
        CandidateScore(
            provider_id="omniroute_freellmapi",
            model_id="large",
            disposition=CandidateDisposition.ELIGIBLE,
            estimated_cost_micros=0,
            score=70,
            quality_factor=70,
            reliability_factor=70,
            latency_factor=60,
            cost_factor=100,
            preference_factor=0,
            trust_factor=100,
        ),
    )
    fake_request_payload = {"task": "demo"}
    decision = RoutingDecision(
        decision_id="routing_decision_test",
        request_id="req-1",
        request_fingerprint=_fingerprint(fake_request_payload),
        selected_provider_id="omniroute_titan_ollama",
        selected_model_id="lightweight",
        candidates=candidates,
        estimated_cost_micros=0,
        budget_limit_micros=0,
        policy_outcome=RoutingPolicyOutcome.FREE,
        fallback_chain=("omniroute_freellmapi/large",),
        created_at=1_000,
    )

    request = ModelExecutionRequest(
        execution_id="exec-1",
        idempotency_key="idem-1",
        project_id="proj-1",
        task_id="task-1",
        request_id="req-1",
        routing_decision=decision,
        selected_provider_id="omniroute_titan_ollama",
        selected_model_id="lightweight",
        input_reference="input://req",
        timeout_seconds=30,
        maximum_attempts=2,
        requested_at=1_000,
    )

    service = GovernedModelExecutionService(
        adapters=(local_adapter, remote_adapter),
        store=InMemoryModelExecutionStore(),
    )
    evidence = service.execute(request, timestamp=1_000)

    assert evidence.state == ModelExecutionState.SUCCEEDED
    assert evidence.attempted_models == (
        "omniroute_titan_ollama/lightweight",
        "omniroute_freellmapi/large",
    )
    assert evidence.fallback_progression == ("omniroute_freellmapi/large",)
    assert evidence.output_reference is not None
    assert len(failing_transport.calls) == 1
    assert len(succeeding_transport.calls) == 1


def test_governed_execution_rejects_when_no_adapter_registered_for_provider() -> None:
    # An unregistered/unapproved provider_id in a routing decision can never
    # be dispatched -- the adapter set itself is the allowlist, matching
    # hermes_cli.prime.dispatch_gate's documented invariant.
    candidates = (
        CandidateScore(
            provider_id="omniroute_unapproved",
            model_id="whatever",
            disposition=CandidateDisposition.ELIGIBLE,
            estimated_cost_micros=0,
            score=50,
            quality_factor=50,
            reliability_factor=50,
            latency_factor=50,
            cost_factor=50,
            preference_factor=50,
            trust_factor=50,
        ),
    )
    fake_request_payload = {"task": "demo2"}
    decision = RoutingDecision(
        decision_id="routing_decision_test2",
        request_id="req-2",
        request_fingerprint=_fingerprint(fake_request_payload),
        selected_provider_id="omniroute_unapproved",
        selected_model_id="whatever",
        candidates=candidates,
        estimated_cost_micros=0,
        budget_limit_micros=0,
        policy_outcome=RoutingPolicyOutcome.FREE,
        fallback_chain=(),
        created_at=1_000,
    )
    request = ModelExecutionRequest(
        execution_id="exec-2",
        idempotency_key="idem-2",
        project_id="proj-1",
        task_id="task-1",
        request_id="req-2",
        routing_decision=decision,
        selected_provider_id="omniroute_unapproved",
        selected_model_id="whatever",
        input_reference="input://req",
        timeout_seconds=30,
        maximum_attempts=1,
        requested_at=1_000,
    )
    service = GovernedModelExecutionService(
        adapters=(), store=InMemoryModelExecutionStore()
    )
    evidence = service.execute(request, timestamp=1_000)
    assert evidence.state in (ModelExecutionState.FAILED, ModelExecutionState.EXHAUSTED)
    assert evidence.output_reference is None
