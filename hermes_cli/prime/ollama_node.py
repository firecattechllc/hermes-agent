"""Governed Ollama-compatible node adapters for Titan and Mac fleet nodes.

Fleet Unification live-runtime work. ``apps/sigil/src/sigil/ai/mac_ollama.py``
already implements a governed Ollama adapter, but it is locked to Sigil's own
four approved model names and is Sigil-specific. This module is the
general-purpose counterpart used at the ``hermes_cli.prime`` fleet layer for
both Titan (lightweight worker models) and Mac (higher-capability desktop
models): it discovers what a node's Ollama endpoint actually has installed,
resolves an *explicitly configured* alias to a concrete model tag, and
refuses to dispatch when that configuration is incomplete.

The specific failure this module exists to prevent: sending a provider
request with a blank ``model`` field, which is what produces the generic
``HTTP 400 model is required`` failure class this fleet was seeing. Every
path through :class:`OllamaNodeConfig.resolve_model` and
:class:`OllamaNodeProviderAdapter.generate` validates that both the alias and
the resolved concrete model name are non-empty *before* any network request
is made — an unconfigured or malformed model never reaches the transport
layer at all.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Protocol, Tuple, cast


class OllamaNodeConfigurationError(ValueError):
    """A governed Ollama node adapter configuration is invalid or incomplete."""


class OllamaNodeTransportError(RuntimeError):
    """The Ollama endpoint could not be reached or returned malformed data."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class OllamaTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> object: ...

    def post(self, url: str, payload: dict, *, timeout_seconds: float) -> object: ...


class UrllibOllamaTransport:
    """Real HTTP transport. Performs no in-process mocking of any kind."""

    def get(self, url: str, *, timeout_seconds: float) -> object:
        request = urllib.request.Request(url, method="GET")
        return self._send(request, timeout_seconds)

    def post(self, url: str, payload: dict, *, timeout_seconds: float) -> object:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json"}
        )
        return self._send(request, timeout_seconds)

    @staticmethod
    def _send(request: urllib.request.Request, timeout_seconds: float) -> object:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise OllamaNodeTransportError(
                f"Ollama endpoint returned HTTP {error.code}", retryable=error.code >= 500
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise OllamaNodeTransportError(str(error), retryable=True) from error
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise OllamaNodeTransportError("malformed Ollama response", retryable=False) from error


def _validate_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise OllamaNodeConfigurationError("Ollama node endpoint must be an http(s) URL")
    if not parsed.hostname:
        raise OllamaNodeConfigurationError("Ollama node endpoint must declare a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise OllamaNodeConfigurationError("Ollama node endpoint must not embed credentials")
    return endpoint


@dataclass(frozen=True, slots=True)
class OllamaNodeConfig:
    """Explicit, validated configuration for one fleet node's Ollama endpoint.

    ``model_aliases`` maps a stable, governed alias (e.g. ``"lightweight"``,
    ``"primary_reasoning"``, ``"embedding"``) to the concrete Ollama model
    tag currently approved for that alias on this node. There is no implicit
    "just use whatever the node has installed" behavior — an alias with no
    entry, or an entry that is blank, is a configuration error, not a
    fallback opportunity.
    """

    natural_key: str
    endpoint: str
    model_aliases: Mapping[str, str] = field(default_factory=dict)
    timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        if not self.natural_key.strip():
            raise OllamaNodeConfigurationError("Ollama node natural_key cannot be blank")
        _validate_endpoint(self.endpoint)
        for alias, model in self.model_aliases.items():
            if not alias.strip() or not model.strip():
                raise OllamaNodeConfigurationError(
                    "Ollama model alias and target model name must both be non-empty"
                )
        if not 100 <= self.timeout_ms <= 300_000:
            raise OllamaNodeConfigurationError("Ollama node timeout is outside its governed bound")

    def resolve_model(self, alias: Optional[str]) -> str:
        """Resolve a governed alias to a concrete model tag. Fail closed.

        Raises :class:`OllamaNodeConfigurationError` for a blank alias, an
        alias with no configured mapping, or a mapping that resolves to a
        blank model name — this is the single choke point that guarantees a
        dispatch attempt never reaches the network with an empty model.
        """
        if alias is None or not alias.strip():
            raise OllamaNodeConfigurationError("model alias must be a non-empty string")
        model = self.model_aliases.get(alias)
        if model is None or not model.strip():
            raise OllamaNodeConfigurationError(
                f"no admitted model is configured for alias {alias!r} on node "
                f"{self.natural_key!r}"
            )
        return model


class OllamaNodeInspector:
    """Discovers what a node's Ollama endpoint actually has installed.

    Never assumes a model exists — ``list_models`` returns an empty tuple
    (not an exception, not a cached optimistic guess) whenever the endpoint
    is unreachable or returns something unexpected, so a caller can tell
    "nothing is known to be installed" from "everything is fine" without a
    try/except of its own.
    """

    def __init__(self, config: OllamaNodeConfig, transport: Optional[OllamaTransport] = None) -> None:
        self.config = config
        self.transport = transport or UrllibOllamaTransport()

    def list_models(self) -> Tuple[str, ...]:
        try:
            raw = self.transport.get(
                f"{self.config.endpoint.rstrip('/')}/api/tags",
                timeout_seconds=self.config.timeout_ms / 1_000,
            )
        except OllamaNodeTransportError:
            return ()
        if not isinstance(raw, dict):
            return ()
        models = cast(Dict[str, object], raw).get("models")
        if not isinstance(models, list):
            return ()
        names: set[str] = set()
        for item in models:
            if not isinstance(item, dict):
                continue
            name = cast(Dict[str, object], item).get("name")
            if isinstance(name, str) and name:
                names.add(name)
        return tuple(sorted(names))

    def is_model_available(self, model: str) -> bool:
        return bool(model) and model in self.list_models()

    def status(self) -> Dict[str, object]:
        """A model-inventory report suitable for a heartbeat submission."""
        installed = self.list_models()
        roles = {}
        for alias, model in self.config.model_aliases.items():
            roles[alias] = {
                "configured_model": model,
                "installed": model in installed,
            }
        return {
            "natural_key": self.config.natural_key,
            "endpoint": self.config.endpoint,
            "installed_models": installed,
            "configured_aliases": roles,
        }


@dataclass(frozen=True, slots=True)
class OllamaGenerateOutcome:
    succeeded: bool
    output_text: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False


class OllamaOutputStore:
    """Minimal content-addressed reference store for generated text.

    Model execution evidence (``hermes_cli.agent_roles.model_execution``)
    only ever stores *references* to output, never raw content, so no
    generated text is embedded in evidence or telemetry. This store is what
    turns raw Ollama output into a reference of that shape
    (``output://ollama-node/...``) while keeping the actual text retrievable
    by whichever caller holds the reference.
    """

    def __init__(self) -> None:
        self._by_reference: Dict[str, str] = {}

    def store(self, natural_key: str, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        reference = f"output://ollama-node/{natural_key}/{digest}"
        self._by_reference[reference] = text
        return reference

    def retrieve(self, reference: str) -> Optional[str]:
        return self._by_reference.get(reference)


class OllamaNodeProviderAdapter:
    """Governed generation adapter for one fleet node's Ollama endpoint.

    This is a device-level adapter (it knows how to talk to one node's
    Ollama server), not a ``hermes_cli.agent_roles.model_execution.ModelProviderAdapter``
    itself — :mod:`hermes_cli.prime.dispatch_gate` wraps this with the
    fleet-admission check and adapts it to that protocol. Keeping the two
    concerns separate means this adapter can be exercised and tested
    (including against a real Ollama instance) without needing a fleet
    runtime, admission, or evidence store in the loop at all.
    """

    def __init__(
        self,
        config: OllamaNodeConfig,
        *,
        transport: Optional[OllamaTransport] = None,
        inspector: Optional[OllamaNodeInspector] = None,
        output_store: Optional[OllamaOutputStore] = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibOllamaTransport()
        self.inspector = inspector or OllamaNodeInspector(config, self.transport)
        self.output_store = output_store or OllamaOutputStore()

    def generate(
        self, *, alias: Optional[str], input_text: str, timeout_seconds: float
    ) -> OllamaGenerateOutcome:
        try:
            model = self.config.resolve_model(alias)
        except OllamaNodeConfigurationError as error:
            return OllamaGenerateOutcome(succeeded=False, error=str(error), retryable=False)

        if not self.inspector.is_model_available(model):
            return OllamaGenerateOutcome(
                succeeded=False,
                error=f"model {model!r} is not installed on node {self.config.natural_key!r}",
                retryable=False,
            )

        try:
            raw = self.transport.post(
                f"{self.config.endpoint.rstrip('/')}/api/generate",
                {"model": model, "prompt": input_text, "stream": False},
                timeout_seconds=min(timeout_seconds, self.config.timeout_ms / 1_000),
            )
        except OllamaNodeTransportError as error:
            return OllamaGenerateOutcome(succeeded=False, error=str(error), retryable=error.retryable)

        if not isinstance(raw, dict):
            return OllamaGenerateOutcome(
                succeeded=False, error="malformed Ollama generate response", retryable=False
            )
        output_text = cast(Dict[str, object], raw).get("response")
        if not isinstance(output_text, str):
            return OllamaGenerateOutcome(
                succeeded=False, error="malformed Ollama generate response", retryable=False
            )

        return OllamaGenerateOutcome(succeeded=True, output_text=output_text)
