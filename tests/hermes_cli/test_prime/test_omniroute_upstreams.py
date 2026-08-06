from __future__ import annotations

import pytest

from hermes_cli.prime.ollama_node import OllamaGenerateOutcome
from hermes_cli.prime.omniroute_upstreams import (
    CircuitBreaker,
    CircuitState,
    FreeLLMAPIUpstreamAdapter,
    TitanOllamaUpstreamAdapter,
    UpstreamTransportError,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── CircuitBreaker ────────────────────────────────────────────────────────────


def test_circuit_breaker_opens_after_threshold_consecutive_failures() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10, clock=clock)
    assert breaker.allow_request() is True
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_circuit_breaker_half_opens_after_cooldown_then_closes_on_success() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    clock.advance(5)
    assert breaker.state == CircuitState.OPEN
    clock.advance(6)
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.allow_request() is True
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_circuit_breaker_success_resets_consecutive_failure_count() -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert (
        breaker.state == CircuitState.CLOSED
    )  # only 1 consecutive failure since reset


def test_circuit_breaker_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(cooldown_seconds=0)


# ── FreeLLMAPIUpstreamAdapter ────────────────────────────────────────────────


class FakeJsonHttpTransport:
    def __init__(self, *, responses=None, errors=None):
        self._responses = list(responses or [])
        self._errors = list(errors or [])
        self.calls = []

    def post_json(self, url, payload, *, headers, timeout_seconds):
        self.calls.append((url, payload, headers))
        if self._errors:
            raise self._errors.pop(0)
        return self._responses.pop(0)


def _chat_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_freellmapi_adapter_success_path() -> None:
    transport = FakeJsonHttpTransport(responses=[_chat_response("hello there")])
    adapter = FreeLLMAPIUpstreamAdapter(
        base_url="http://127.0.0.1:3002",
        api_key="secret-key",
        timeout_ms=5_000,
        retry_limit=2,
        transport=transport,
    )
    outcome = adapter.generate(model="gpt-4o-mini", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is True
    assert outcome.output_text == "hello there"
    assert transport.calls[0][2]["Authorization"] == "Bearer secret-key"


def test_freellmapi_adapter_bounded_retry_never_exceeds_retry_limit_plus_one() -> None:
    transport = FakeJsonHttpTransport(
        errors=[
            UpstreamTransportError("down", retryable=True),
            UpstreamTransportError("down", retryable=True),
            UpstreamTransportError("down", retryable=True),
        ],
    )
    adapter = FreeLLMAPIUpstreamAdapter(
        base_url="http://127.0.0.1:3002",
        api_key=None,
        timeout_ms=5_000,
        retry_limit=2,
        transport=transport,
    )
    outcome = adapter.generate(model="gpt-4o-mini", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert outcome.attempts == 3  # retry_limit(2) + 1 initial attempt, never unbounded
    assert len(transport.calls) == 3


def test_freellmapi_adapter_non_retryable_error_stops_immediately() -> None:
    transport = FakeJsonHttpTransport(
        errors=[UpstreamTransportError("bad request", retryable=False)]
    )
    adapter = FreeLLMAPIUpstreamAdapter(
        base_url="http://127.0.0.1:3002",
        api_key=None,
        timeout_ms=5_000,
        retry_limit=3,
        transport=transport,
    )
    outcome = adapter.generate(model="gpt-4o-mini", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert outcome.attempts == 1
    assert len(transport.calls) == 1


def test_freellmapi_adapter_malformed_response_is_not_retryable() -> None:
    transport = FakeJsonHttpTransport(responses=[{"unexpected": "shape"}])
    adapter = FreeLLMAPIUpstreamAdapter(
        base_url="http://127.0.0.1:3002",
        api_key=None,
        timeout_ms=5_000,
        retry_limit=3,
        transport=transport,
    )
    outcome = adapter.generate(model="gpt-4o-mini", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert len(transport.calls) == 1


def test_freellmapi_adapter_opens_circuit_and_then_rejects_without_network_call() -> (
    None
):
    transport = FakeJsonHttpTransport(
        errors=[UpstreamTransportError("down", retryable=True)] * 10,
    )
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=9999)
    adapter = FreeLLMAPIUpstreamAdapter(
        base_url="http://127.0.0.1:3002",
        api_key=None,
        timeout_ms=5_000,
        retry_limit=0,
        circuit_breaker=breaker,
        transport=transport,
    )
    first = adapter.generate(model="gpt-4o-mini", input_text="hi", timeout_seconds=5)
    assert first.succeeded is False
    calls_after_first = len(transport.calls)

    second = adapter.generate(model="gpt-4o-mini", input_text="hi", timeout_seconds=5)
    assert second.succeeded is False
    assert "circuit breaker is open" in second.error
    assert len(transport.calls) == calls_after_first  # no new network call was made


def test_freellmapi_adapter_rejects_blank_model_without_network_call() -> None:
    transport = FakeJsonHttpTransport()
    adapter = FreeLLMAPIUpstreamAdapter(
        base_url="http://127.0.0.1:3002",
        api_key=None,
        timeout_ms=5_000,
        retry_limit=1,
        transport=transport,
    )
    outcome = adapter.generate(model="", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert transport.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(timeout_ms=10),
        dict(retry_limit=99),
        dict(base_url="not-a-url"),
    ],
)
def test_freellmapi_adapter_rejects_invalid_construction(kwargs) -> None:
    defaults = dict(
        base_url="http://127.0.0.1:3002", api_key=None, timeout_ms=5_000, retry_limit=1
    )
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        FreeLLMAPIUpstreamAdapter(**defaults)


# ── TitanOllamaUpstreamAdapter ───────────────────────────────────────────────


class FakeOllamaUnderlying:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def generate(self, *, alias, input_text, timeout_seconds):
        self.calls.append(alias)
        return self._outcomes.pop(0)


def test_titan_ollama_adapter_success_path() -> None:
    underlying = FakeOllamaUnderlying([
        OllamaGenerateOutcome(succeeded=True, output_text="local reply")
    ])
    adapter = TitanOllamaUpstreamAdapter(
        node_config=None, retry_limit=1, underlying=underlying
    )
    outcome = adapter.generate(model="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is True
    assert outcome.output_text == "local reply"
    assert len(underlying.calls) == 1


def test_titan_ollama_adapter_bounded_retry() -> None:
    underlying = FakeOllamaUnderlying([
        OllamaGenerateOutcome(succeeded=False, error="down", retryable=True),
        OllamaGenerateOutcome(succeeded=False, error="down", retryable=True),
        OllamaGenerateOutcome(succeeded=True, output_text="recovered"),
    ])
    adapter = TitanOllamaUpstreamAdapter(
        node_config=None, retry_limit=2, underlying=underlying
    )
    outcome = adapter.generate(model="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is True
    assert outcome.attempts == 3
    assert len(underlying.calls) == 3


def test_titan_ollama_adapter_non_retryable_stops_immediately() -> None:
    underlying = FakeOllamaUnderlying([
        OllamaGenerateOutcome(
            succeeded=False, error="model not installed", retryable=False
        ),
    ])
    adapter = TitanOllamaUpstreamAdapter(
        node_config=None, retry_limit=3, underlying=underlying
    )
    outcome = adapter.generate(model="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert len(underlying.calls) == 1


def test_titan_ollama_adapter_circuit_breaker_prevents_call_when_open() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=9999)
    underlying = FakeOllamaUnderlying([
        OllamaGenerateOutcome(succeeded=False, error="down", retryable=True),
    ])
    adapter = TitanOllamaUpstreamAdapter(
        node_config=None,
        retry_limit=0,
        circuit_breaker=breaker,
        underlying=underlying,
    )
    first = adapter.generate(model="lightweight", input_text="hi", timeout_seconds=5)
    assert first.succeeded is False
    assert len(underlying.calls) == 1

    second = adapter.generate(model="lightweight", input_text="hi", timeout_seconds=5)
    assert second.succeeded is False
    assert "circuit breaker is open" in second.error
    assert len(underlying.calls) == 1  # underlying was never invoked again
