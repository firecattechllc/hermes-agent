"""Bounded-retry, circuit-breaking upstream transport for OmniRoute.

OmniRoute owns provider transport, retries, failover, and latency/
availability handling between Titan's local Ollama and FreeLLMAPI (see
``docs/architecture/hydra-ecosystem/evidence/TITAN_DISCOVERY.md`` — FreeLLMAPI
already runs as a healthy container on Titan). This module is that
transport layer: every upstream call goes through a bounded retry loop (no
unbounded/infinite retry is representable — the loop always terminates
after ``retry_limit + 1`` attempts) guarded by a per-upstream
:class:`CircuitBreaker`, so a wedged FreeLLMAPI upstream degrades to fast
failures instead of hanging every subsequent request or crashing the
process that called it.

Titan Ollama transport reuses :mod:`hermes_cli.prime.ollama_node` unmodified
(``OllamaNodeProviderAdapter``) rather than re-implementing Ollama's wire
protocol — this module only adds the bounded-retry/circuit-breaker envelope
around it, matching how :mod:`hermes_cli.prime.dispatch_gate` composes in
front of pre-existing subsystems instead of editing them.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping, Optional, Protocol, cast

from hermes_cli.prime.ollama_node import OllamaNodeConfig, OllamaNodeProviderAdapter


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """A minimal, deterministic circuit breaker with an injectable clock.

    Opens after ``failure_threshold`` consecutive failures. Once open, every
    call is rejected without touching the network until ``cooldown_seconds``
    has elapsed, at which point exactly one call is allowed through
    (half-open) to probe recovery; that call's outcome decides whether the
    breaker closes again or re-opens for another cooldown.
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _opened_at: Optional[float] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.cooldown_seconds < 1:
            raise ValueError("cooldown_seconds must be >= 1")

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if self.clock() - self._opened_at >= self.cooldown_seconds:
                return CircuitState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        return self.state != CircuitState.OPEN

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self.clock()


@dataclass(frozen=True, slots=True)
class UpstreamOutcome:
    succeeded: bool
    output_text: Optional[str] = None
    model_used: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    latency_ms: int = 0
    attempts: int = 0


class UpstreamTransportError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class JsonHttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: dict,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object: ...


class UrllibJsonHttpTransport:
    """Real HTTP transport. Never logs headers (which may carry an
    ``Authorization`` bearer token) — only the caller-supplied URL path is
    ever mentioned in a raised error."""

    def post_json(
        self,
        url: str,
        payload: dict,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise UpstreamTransportError(
                f"upstream returned HTTP {error.code}",
                retryable=error.code >= 500 or error.code == 429,
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UpstreamTransportError(
                "upstream unreachable or timed out", retryable=True
            ) from error
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise UpstreamTransportError(
                "malformed upstream response", retryable=False
            ) from error


def _validate_http_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("upstream endpoint must be an http(s) URL")
    if not parsed.hostname:
        raise ValueError("upstream endpoint must declare a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("upstream endpoint must not embed credentials")
    return endpoint


def _extract_openai_chat_text(raw: object) -> Optional[str]:
    if not isinstance(raw, dict):
        return None
    choices = cast(dict, raw).get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


class FreeLLMAPIUpstreamAdapter:
    """Governed transport to the FreeLLMAPI container running on Titan.

    FreeLLMAPI is treated purely as an OpenAI-compatible upstream: this
    adapter never assumes it is reachable (bounded retry + circuit breaker
    on every call) and never lets its outage propagate as anything other
    than a normal, classifiable :class:`UpstreamOutcome` failure — a caller
    (:mod:`hermes_cli.prime.omniroute_server`) can always still serve
    Titan Ollama when this adapter is failing.
    """

    provider_id = "freellmapi"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str],
        timeout_ms: int,
        retry_limit: int,
        circuit_breaker: Optional[CircuitBreaker] = None,
        transport: Optional[JsonHttpTransport] = None,
    ) -> None:
        self.base_url = _validate_http_endpoint(base_url)
        if not 100 <= timeout_ms <= 120_000:
            raise ValueError("timeout_ms must be between 100 and 120000")
        if not 0 <= retry_limit <= 5:
            raise ValueError("retry_limit must be between 0 and 5")
        self._api_key = api_key
        self.timeout_ms = timeout_ms
        self.retry_limit = retry_limit
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.transport = transport or UrllibJsonHttpTransport()

    def generate(
        self, *, model: str, input_text: str, timeout_seconds: float
    ) -> UpstreamOutcome:
        if not model or not model.strip():
            return UpstreamOutcome(
                succeeded=False, error="model is required", retryable=False
            )
        if not self.circuit_breaker.allow_request():
            return UpstreamOutcome(
                succeeded=False,
                error="freellmapi circuit breaker is open",
                retryable=True,
            )

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": input_text}],
            "stream": False,
        }
        effective_timeout = min(timeout_seconds, self.timeout_ms / 1_000)

        attempts = 0
        last_error, last_retryable = "unknown upstream error", True
        started = time.monotonic()
        while attempts <= self.retry_limit:
            attempts += 1
            try:
                raw = self.transport.post_json(
                    f"{self.base_url.rstrip('/')}/v1/chat/completions",
                    payload,
                    headers=headers,
                    timeout_seconds=effective_timeout,
                )
            except UpstreamTransportError as error:
                last_error, last_retryable = str(error), error.retryable
                if not error.retryable:
                    break
                continue

            text = _extract_openai_chat_text(raw)
            if text is None:
                last_error, last_retryable = "malformed FreeLLMAPI response", False
                break

            self.circuit_breaker.record_success()
            return UpstreamOutcome(
                succeeded=True,
                output_text=text,
                model_used=model,
                latency_ms=int((time.monotonic() - started) * 1_000),
                attempts=attempts,
            )

        self.circuit_breaker.record_failure()
        return UpstreamOutcome(
            succeeded=False,
            error=last_error,
            retryable=last_retryable,
            latency_ms=int((time.monotonic() - started) * 1_000),
            attempts=attempts,
        )


class TitanOllamaUpstreamAdapter:
    """Governed transport to Titan's own local Ollama endpoint.

    This is Titan's offline-capable route: it never leaves the host, has no
    internet dependency, and (per
    ``docs/architecture/OLLAMA_ROUTING_BOUNDARY.md``) is reached exclusively
    through :class:`hermes_cli.prime.ollama_node.OllamaNodeProviderAdapter`
    — this class only adds the bounded-retry/circuit-breaker envelope that
    OmniRoute needs on top of it.
    """

    provider_id = "titan_ollama"

    def __init__(
        self,
        *,
        node_config: OllamaNodeConfig,
        retry_limit: int,
        circuit_breaker: Optional[CircuitBreaker] = None,
        underlying: Optional[OllamaNodeProviderAdapter] = None,
    ) -> None:
        if not 0 <= retry_limit <= 5:
            raise ValueError("retry_limit must be between 0 and 5")
        self.retry_limit = retry_limit
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self._underlying = underlying or OllamaNodeProviderAdapter(node_config)

    def generate(
        self, *, model: str, input_text: str, timeout_seconds: float
    ) -> UpstreamOutcome:
        if not self.circuit_breaker.allow_request():
            return UpstreamOutcome(
                succeeded=False,
                error="titan_ollama circuit breaker is open",
                retryable=True,
            )

        attempts = 0
        last_error, last_retryable = "unknown ollama error", True
        started = time.monotonic()
        while attempts <= self.retry_limit:
            attempts += 1
            outcome = self._underlying.generate(
                alias=model, input_text=input_text, timeout_seconds=timeout_seconds
            )
            if outcome.succeeded:
                self.circuit_breaker.record_success()
                return UpstreamOutcome(
                    succeeded=True,
                    output_text=outcome.output_text,
                    model_used=model,
                    latency_ms=int((time.monotonic() - started) * 1_000),
                    attempts=attempts,
                )
            last_error, last_retryable = (
                outcome.error or "ollama generation failed",
                outcome.retryable,
            )
            if not outcome.retryable:
                break

        self.circuit_breaker.record_failure()
        return UpstreamOutcome(
            succeeded=False,
            error=last_error,
            retryable=last_retryable,
            latency_ms=int((time.monotonic() - started) * 1_000),
            attempts=attempts,
        )
