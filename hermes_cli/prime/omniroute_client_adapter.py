"""Hermes-side governed adapter for Titan's local OmniRoute HTTP service.

Connects OmniRoute into the pre-existing governed execution pipeline
(:mod:`hermes_cli.agent_roles.model_execution`) exactly the way
:class:`hermes_cli.prime.dispatch_gate.PrimeGovernedProviderAdapter` connects
a fleet node adapter into it — by implementing the same
``ModelProviderAdapter`` protocol (``provider_id`` + ``execute``) rather than
adding a second execution path. ``GovernedModelExecutionService`` does not
need to know, and is not told, that its adapter happens to call an
OpenAI-compatible HTTP endpoint instead of a fleet node directly; every
budget, approval, and fallback behavior it already has applies unchanged.

This module never modifies ``model_execution.py``. It only composes in front
of it.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Optional, Protocol

from hermes_cli.agent_roles.model_execution import (
    ModelExecutionErrorClass,
    ProviderExecutionResult,
    ProviderUsage,
)


class OmniRouteClientTransportError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class OmniRouteClientTransport(Protocol):
    def post_chat_completion(
        self, url: str, payload: dict, *, auth_token: str, timeout_seconds: float
    ) -> object: ...


class UrllibOmniRouteClientTransport:
    """Real HTTP transport to the local OmniRoute service. Never logs the
    bearer token — only the request path is ever mentioned in an error."""

    def post_chat_completion(
        self, url: str, payload: dict, *, auth_token: str, timeout_seconds: float
    ) -> object:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise OmniRouteClientTransportError(
                f"OmniRoute returned HTTP {error.code}", retryable=error.code >= 500
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise OmniRouteClientTransportError(
                "OmniRoute endpoint unreachable or timed out", retryable=True
            ) from error
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise OmniRouteClientTransportError(
                "malformed OmniRoute response", retryable=False
            ) from error


class OmniRouteInputResolver(Protocol):
    def __call__(self, input_reference: str) -> Optional[str]: ...


class OmniRouteHTTPProviderAdapter:
    """Implements ``ModelProviderAdapter`` by calling the local OmniRoute
    OpenAI-compatible endpoint.

    ``provider_id`` here identifies the *governed routing lane* Hermes
    selected (``"omniroute_titan_ollama"`` or ``"omniroute_freellmapi"``),
    not a raw provider name — the actual upstream selection (Titan Ollama vs
    FreeLLMAPI) happens inside OmniRoute itself, which is exactly where the
    architecture places that responsibility. This adapter only carries the
    caller's alias through so OmniRoute's own policy resolves it identically
    to how it would if invoked directly.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        auth_token: str,
        input_resolver: OmniRouteInputResolver,
        transport: Optional[OmniRouteClientTransport] = None,
    ) -> None:
        if not provider_id or not provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not base_url or not base_url.strip():
            raise ValueError("base_url must be non-empty")
        if not auth_token or len(auth_token) < 16:
            raise ValueError("auth_token must be at least 16 characters")
        self._provider_id = provider_id
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._input_resolver = input_resolver
        self._transport = transport or UrllibOmniRouteClientTransport()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def execute(
        self, *, model_id: str, input_reference: str, timeout_seconds: int
    ) -> ProviderExecutionResult:
        if not model_id or not model_id.strip():
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.INVALID_REQUEST
            )

        input_text = self._input_resolver(input_reference)
        if input_text is None:
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.INVALID_REQUEST
            )

        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": input_text}],
            "stream": False,
        }
        try:
            raw = self._transport.post_chat_completion(
                f"{self._base_url}/v1/chat/completions",
                payload,
                auth_token=self._auth_token,
                timeout_seconds=timeout_seconds,
            )
        except OmniRouteClientTransportError as error:
            classification = (
                ModelExecutionErrorClass.TIMEOUT
                if "timed out" in str(error)
                else ModelExecutionErrorClass.PROVIDER_UNAVAILABLE
                if error.retryable
                else ModelExecutionErrorClass.PERMANENT_PROVIDER_ERROR
            )
            return ProviderExecutionResult(error_classification=classification)

        text = extract_output_text(raw)
        if text is None:
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.OUTPUT_VALIDATION_FAILED
            )

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        output_reference = f"output://omniroute/{self._provider_id}/{digest}"
        usage = ProviderUsage(
            input_units=len(input_text.split()),
            output_units=len(text.split()),
            actual_cost_micros=0,
        )
        return ProviderExecutionResult(output_reference=output_reference, usage=usage)


def extract_output_text(raw: object) -> Optional[str]:
    if not isinstance(raw, dict):
        return None
    choices = raw.get("choices")
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
