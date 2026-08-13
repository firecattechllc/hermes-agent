"""The single, protocol-generic :class:`~runway.omniroute_port.ExecutionPort`
implementation for every channel — this is what "generalize instead of one
bespoke adapter per provider" means concretely. Provider-specific behavior
lives entirely in :class:`~runway.providers.gateway.models.ChannelConfig`
(base_url, protocol, model_overrides) and in the small per-protocol modules
under ``protocols/``, never in a new adapter class.

Same safety properties as the retired ``runway.providers.laozhang`` module:
exactly one HTTP request per :meth:`execute` call, no automatic retries, and
a refusal to run at all unless ``flags.external_execution_enabled`` is
``True`` — which cannot currently be constructed (``runway/flags.py``).
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request

from runway.flags import RunwayFeatureFlags
from runway.omniroute_port import ExecutionRequest, ExecutionResult
from runway.providers.credentials import CredentialResolutionError, CredentialResolver
from runway.providers.errors import ExecutionFailureCategory
from runway.providers.gateway import protocol as protocol_module
from runway.providers.gateway import protocols as _protocols  # noqa: F401 -- registers concrete handlers
from runway.providers.gateway.registry import ChannelRegistry

logger = logging.getLogger(__name__)


def _upstream_model_id(model_key: str, provider_id: str, overrides: dict) -> str:
    if model_key in overrides:
        return overrides[model_key]
    if "@" not in model_key:
        raise ValueError(f"model_key {model_key!r} must be of the form '<upstream_model>@<provider_id>'")
    upstream, _, suffix = model_key.partition("@")
    if suffix != provider_id or not upstream:
        raise ValueError(f"model_key {model_key!r} does not belong to provider {provider_id!r}")
    return upstream


class GatewayExecutionAdapter:
    def __init__(
        self, channels: ChannelRegistry, *, flags: RunwayFeatureFlags, credential_resolver: CredentialResolver,
    ) -> None:
        self._channels = channels
        self._flags = flags
        self._credentials = credential_resolver

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self._flags.external_execution_enabled:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED.value,
            )

        channel = self._channels.channel_for_model(request.model_key)
        if channel is None:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.INVALID_REQUEST.value,
            )
        if not channel.enabled or not self._channels.is_authorized(channel.channel_id):
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED.value,
            )
        if not request.messages:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.INVALID_REQUEST.value,
            )
        try:
            upstream_model = _upstream_model_id(request.model_key, channel.provider_id, dict(channel.model_overrides))
        except ValueError:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.INVALID_REQUEST.value,
            )

        try:
            api_key = self._credentials.resolve(channel.credential_ref)
        except CredentialResolutionError:
            logger.warning("gateway execute: credential_ref could not be resolved (no secret logged)")
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.AUTH_FAILED.value,
            )

        handler = protocol_module.handler_for(channel.protocol)
        prepared = handler.build_request(
            base_url=channel.base_url, api_key=api_key, upstream_model=upstream_model,
            messages=request.messages, max_output_tokens=request.usage_estimate.output_tokens,
        )
        del api_key  # not referenced again in this frame

        http_request = urllib.request.Request(prepared.url, data=prepared.body, method="POST")
        for key, value in prepared.headers.items():
            http_request.add_header(key, value)
        http_request.add_header("User-Agent", "hermes-runway/1")

        started = time.monotonic()
        try:
            with urllib.request.urlopen(http_request, timeout=channel.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            category = protocol_module.categorize_http_status(exc.code)
            logger.warning("gateway execute failed: http_status=%s category=%s", exc.code, category.value)
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms, error=category.value,
            )
        except TimeoutError:
            latency_ms = int((time.monotonic() - started) * 1000)
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms,
                error=ExecutionFailureCategory.TIMEOUT.value,
            )
        except urllib.error.URLError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            category = (
                ExecutionFailureCategory.TIMEOUT if isinstance(exc.reason, TimeoutError)
                else ExecutionFailureCategory.NETWORK_FAILURE
            )
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms, error=category.value,
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        parsed = handler.parse_success(raw)
        if not parsed.succeeded:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms,
                error=parsed.error or ExecutionFailureCategory.MALFORMED_RESPONSE.value,
                reported_model=parsed.reported_model,
            )
        return ExecutionResult(
            decision_id=request.decision_id, succeeded=True, latency_ms=latency_ms,
            actual_input_tokens=parsed.input_tokens, actual_output_tokens=parsed.output_tokens,
            reported_model=parsed.reported_model, provider_request_id=parsed.provider_request_id,
        )
