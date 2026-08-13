"""LaoZhang API execution adapter — the first real provider commissioned
into Runway.

LaoZhang (https://www.laozhang.ai/, docs at docs.laozhang.ai) is an
OpenAI-compatible multi-model gateway/reseller — an aggregator that proxies
200+ upstream models behind one API surface, not a first-party model vendor.
That provenance profile is exactly what ``runway.trust`` calls Tier D/C
territory, not Tier A — see :mod:`runway.trust` and Phase F of the
commissioning task for the deliberate tier assignment. Nothing in *this*
module assumes or grants trust; it only knows how to make (or refuse to
make) one well-formed HTTP request.

Confirmed API shape (fetched from docs.laozhang.ai, not guessed):

* ``POST {base_url}/chat/completions`` — OpenAI-compatible chat completions.
* ``GET {base_url}/models`` — OpenAI-compatible model catalog.
* ``Authorization: Bearer <api_key>`` on every request.
* Errors: HTTP status + ``{"error": {"message", "type", "code"}}`` body,
  same shape as OpenAI's.
* ``usage.prompt_tokens`` / ``usage.completion_tokens`` on success.

Safety properties, enforced in code (not just by convention):

* :meth:`LaoZhangExecutionAdapter.execute` makes **exactly one** HTTP
  request — there is no retry loop anywhere in this module.
* :meth:`LaoZhangExecutionAdapter.execute` and :func:`list_advertised_models`
  both refuse to run unless ``flags.external_execution_enabled`` is
  ``True``. As of this commit that can never be constructed
  (``runway/flags.py`` hard-locks it to ``False``) — so this module cannot
  place a live network call in this build, independent of whether a valid
  ``credential_ref`` is configured.
* :func:`run_bounded_live_canary` additionally requires an explicit
  ``dangerously_i_understand_this_spends_real_money=True`` argument — belt
  and suspenders specifically because it is the one function in this repo
  whose entire purpose is to spend real money on a real request.
* The resolved API key exists only inside :meth:`LaoZhangExecutionAdapter.execute`'s
  stack frame; it is never stored on ``self``, never returned, and never
  logged. Only HTTP status codes and error *categories* are logged.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from runway.flags import RunwayFeatureFlags
from runway.identifiers import clean_credential_ref
from runway.omniroute_port import ExecutionRequest, ExecutionResult
from runway.pricing import UsageEstimate
from runway.providers.credentials import CredentialResolutionError, CredentialResolver
from runway.providers.errors import ExecutionFailureCategory
from runway.trust import TrustTier

logger = logging.getLogger(__name__)

PROVIDER_ID = "laozhang"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_CANARY_OUTPUT_TOKENS = 16

#: Deliberate, documented trust-tier recommendation for Phase F. LaoZhang is
#: a multi-provider aggregator/reseller (200+ upstream models behind one
#: API), matching runway.trust's Tier C definition — "multi-provider
#: gateways/aggregators that have passed qualification" — not Tier A/B (no
#: first-party vendor relationship) and not Tier D (its identity and API
#: shape are publicly documented, unlike an unknown/undocumented relay).
#: This constant does not assign anything by itself: a human still
#: constructs the real ProviderRecord and still calls
#: QualificationService.authorize(), and only after real qualification
#: evidence exists (see run_bounded_live_canary).
RECOMMENDED_TRUST_TIER = TrustTier.VETTED_AGGREGATOR


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError(f"LaoZhang base_url must be https, got {value!r}")
    if not parsed.netloc:
        raise ValueError(f"LaoZhang base_url is missing a host: {value!r}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"LaoZhang base_url must not include a query or fragment: {value!r}")
    return value.rstrip("/")


class LaoZhangConfig(BaseModel):
    """Explicit, validated adapter configuration. No default ``base_url`` —
    the operator must state it, per Phase B ("support an explicit base
    URL/configuration value"); this file does not silently pick between
    ``api.laozhang.ai`` and ``api2.laozhang.ai`` on the caller's behalf.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(..., min_length=1)
    credential_ref: str = Field(..., min_length=1)
    timeout_seconds: float = Field(default=DEFAULT_REQUEST_TIMEOUT_SECONDS, gt=0, le=120)
    #: Runway model_key (e.g. "gpt-4o-mini@laozhang") -> upstream model id,
    #: for the rare case the upstream id can't just be derived by stripping
    #: "@laozhang". Empty by default: derivation from model_key is enough
    #: for the normal "<upstream_model>@laozhang" convention.
    model_overrides: Dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _validate_base_url(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str) -> str:
        return clean_credential_ref(value)


def _upstream_model_id(model_key: str, overrides: Dict[str, str]) -> str:
    if model_key in overrides:
        return overrides[model_key]
    if "@" not in model_key:
        raise ValueError(f"model_key {model_key!r} must be of the form '<upstream_model>@<provider_id>'")
    upstream, _, provider_suffix = model_key.partition("@")
    if provider_suffix != PROVIDER_ID:
        raise ValueError(f"model_key {model_key!r} does not belong to provider {PROVIDER_ID!r}")
    if not upstream:
        raise ValueError(f"model_key {model_key!r} has an empty upstream model id")
    return upstream


def _categorize_http_error(exc: urllib.error.HTTPError) -> ExecutionFailureCategory:
    if exc.code in (401, 403):
        return ExecutionFailureCategory.AUTH_FAILED
    if exc.code == 429:
        return ExecutionFailureCategory.RATE_LIMITED
    if exc.code == 400:
        return ExecutionFailureCategory.INVALID_REQUEST
    return ExecutionFailureCategory.PROVIDER_ERROR


class LaoZhangExecutionAdapter:
    def __init__(
        self,
        config: LaoZhangConfig,
        *,
        flags: RunwayFeatureFlags,
        credential_resolver: CredentialResolver,
    ) -> None:
        self._config = config
        self._flags = flags
        self._credentials = credential_resolver

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self._flags.external_execution_enabled:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED.value,
            )
        if request.provider_id != PROVIDER_ID:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.INVALID_REQUEST.value,
            )
        try:
            upstream_model = _upstream_model_id(request.model_key, self._config.model_overrides)
        except ValueError:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.INVALID_REQUEST.value,
            )
        if not request.messages:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.INVALID_REQUEST.value,
            )

        try:
            api_key = self._credentials.resolve(self._config.credential_ref)
        except CredentialResolutionError:
            logger.warning("laozhang execute: credential_ref could not be resolved (no secret logged)")
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False,
                error=ExecutionFailureCategory.AUTH_FAILED.value,
            )

        body: dict = {
            "model": upstream_model,
            "messages": [dict(item) for item in request.messages],
            "stream": False,
        }
        if request.usage_estimate.output_tokens:
            body["max_tokens"] = request.usage_estimate.output_tokens

        url = f"{self._config.base_url}/chat/completions"
        http_request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
        http_request.add_header("Content-Type", "application/json")
        http_request.add_header("User-Agent", "hermes-runway/1")
        http_request.add_header("Authorization", f"Bearer {api_key}")  # never logged, never returned
        del api_key  # not referenced again in this frame

        started = time.monotonic()
        try:
            with urllib.request.urlopen(http_request, timeout=self._config.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            category = _categorize_http_error(exc)
            logger.warning("laozhang execute failed: http_status=%s category=%s", exc.code, category.value)
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms, error=category.value,
            )
        except TimeoutError:
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning("laozhang execute timed out after %sms", latency_ms)
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
            logger.warning("laozhang execute network error: category=%s", category.value)
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms, error=category.value,
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms,
                error=ExecutionFailureCategory.MALFORMED_RESPONSE.value,
            )
        if not isinstance(data, dict):
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms,
                error=ExecutionFailureCategory.MALFORMED_RESPONSE.value,
            )

        choices = data.get("choices")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if not isinstance(choices, list) or not choices:
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms,
                error=ExecutionFailureCategory.MALFORMED_RESPONSE.value,
                reported_model=data.get("model") if isinstance(data.get("model"), str) else None,
            )
        try:
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (TypeError, ValueError):
            return ExecutionResult(
                decision_id=request.decision_id, succeeded=False, latency_ms=latency_ms,
                error=ExecutionFailureCategory.MALFORMED_RESPONSE.value,
            )

        return ExecutionResult(
            decision_id=request.decision_id, succeeded=True, latency_ms=latency_ms,
            actual_input_tokens=max(0, input_tokens), actual_output_tokens=max(0, output_tokens),
            reported_model=data.get("model") if isinstance(data.get("model"), str) else None,
            provider_request_id=data.get("id") if isinstance(data.get("id"), str) else None,
        )


class AdvertisedModel(BaseModel):
    """One entry from LaoZhang's ``GET /models`` catalog. Advisory only —
    see the module and Phase C docs: appearing here is not proof that a
    request to this id is actually served by the vendor it claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    owned_by: str = ""
    created: Optional[int] = None


def list_advertised_models(
    config: LaoZhangConfig, *, flags: RunwayFeatureFlags, credential_resolver: CredentialResolver,
) -> Tuple[AdvertisedModel, ...]:
    """``GET {base_url}/models`` — bounded, on-demand, advisory catalog
    fetch. Never called automatically and never used to auto-populate the
    Runway model registry (discovery is not authorization — see
    ``runway.discovery``); a human decides which advertised ids become real
    ``ModelRecord`` entries.
    """
    if not flags.external_execution_enabled:
        raise PermissionError("list_advertised_models requires external_execution_enabled (currently unavailable)")
    api_key = credential_resolver.resolve(config.credential_ref)
    url = f"{config.base_url}/models"
    http_request = urllib.request.Request(url, method="GET")
    http_request.add_header("Authorization", f"Bearer {api_key}")
    del api_key
    with urllib.request.urlopen(http_request, timeout=config.timeout_seconds) as response:
        raw = response.read()
    data = json.loads(raw)
    items = data.get("data", []) if isinstance(data, dict) else []
    return tuple(
        AdvertisedModel(id=item["id"], owned_by=str(item.get("owned_by", "")), created=item.get("created"))
        for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


class LiveCanaryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    requested_model_key: str
    succeeded: bool
    reported_model: Optional[str] = None
    provider_request_id: Optional[str] = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    identity_note: str = "reported_model is provider-claimed identity only; not independently verified"


def run_bounded_live_canary(
    adapter: LaoZhangExecutionAdapter,
    *,
    model_key: str,
    dangerously_i_understand_this_spends_real_money: bool,
) -> LiveCanaryResult:
    """Exactly one tiny, non-streaming, tool-free, no-retry chat completion.
    Output is hard-capped well below any reasonable per-request budget.
    This is Phase E's bounded live canary — deliberately not the full
    ``runway.canary`` qualification suite, which was designed for a
    synthetic client and would need multiple real calls (structured output,
    tool calling, context probing) to run against a live provider; that
    would violate "the request cannot expand into multiple billable calls."
    """
    if not dangerously_i_understand_this_spends_real_money:
        raise PermissionError(
            "run_bounded_live_canary refuses to run without "
            "dangerously_i_understand_this_spends_real_money=True — this call "
            "sends a real, billable request to a real external provider."
        )
    output_cap = _MAX_CANARY_OUTPUT_TOKENS
    execution_request = ExecutionRequest(
        decision_id=f"laozhang_live_canary_{int(time.time())}",
        provider_id=PROVIDER_ID,
        model_key=model_key,
        usage_estimate=UsageEstimate(input_tokens=8, output_tokens=output_cap),
        messages=({"role": "user", "content": "Reply with exactly one word: pong"},),
    )
    result = adapter.execute(execution_request)
    return LiveCanaryResult(
        provider_id=PROVIDER_ID, requested_model_key=model_key, succeeded=result.succeeded,
        reported_model=result.reported_model, provider_request_id=result.provider_request_id,
        latency_ms=result.latency_ms, input_tokens=result.actual_input_tokens,
        output_tokens=result.actual_output_tokens, error=result.error,
    )
