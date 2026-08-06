"""Titan-local, OpenAI-compatible OmniRoute HTTP service.

This is the one stable Titan-local endpoint approved Hermes workloads call.
OmniRoute owns provider transport, OpenAI-compatible routing, provider
health, retries, and failover among Hermes-approved providers (Titan Ollama
and FreeLLMAPI); Hermes owns which aliases exist and which providers they
may resolve to (:mod:`hermes_cli.prime.omniroute_config`). This module never
accepts a raw provider/model pair from a caller — only a governed alias — so
there is no code path by which a request can reach an unapproved provider.

Built the same way :mod:`hermes_cli.prime.server` builds Prime's control
plane: stdlib ``http.server`` only (no new third-party dependency), bearer
auth compared with ``hmac.compare_digest``, and a bind-host validator that
refuses a wildcard or public address (:meth:`TitanRoutingConfig.__post_init__`
already enforces this before a socket is ever opened).

Every dispatch decision is recorded through
:mod:`hermes_cli.prime.omniroute_evidence` before the response is returned —
including rejections (unknown alias, denied provider, offline-only, budget)
and upstream failures — so this is the single source of "what did OmniRoute
actually do" for Mission Control and audit.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Protocol

from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.omniroute_config import TitanRoutingConfig
from hermes_cli.prime.omniroute_evidence import (
    RouteDecisionEvidence,
    RouteStatus,
    build_route_decision_evidence_record,
)
from hermes_cli.prime.omniroute_health import build_health_snapshot
from hermes_cli.prime.omniroute_upstreams import UpstreamOutcome

logger = logging.getLogger("hermes.prime.omniroute")

MAX_REQUEST_BYTES = 1_048_576


class OmniRouteServerConfigError(ValueError):
    """OmniRoute HTTP server configuration is invalid."""


class UpstreamAdapter(Protocol):
    provider_id: str

    def generate(
        self, *, model: str, input_text: str, timeout_seconds: float
    ) -> UpstreamOutcome: ...

    @property
    def circuit_breaker(self) -> object: ...


class BudgetTracker:
    """Bounded, thread-safe daily/per-request spend tracking.

    Both default providers (Titan Ollama, FreeLLMAPI) are free, so
    ``actual_cost_micros`` is always ``0`` for every real dispatch today —
    this tracker exists so the required daily/request budget controls are a
    real, enforced mechanism (not a config field nobody reads) the moment a
    paid provider is ever added, rather than something bolted on later.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._daily_spent_micros = 0
        self._day_epoch: Optional[int] = None

    def _roll_day(self, now: int) -> None:
        day = now // 86_400
        if self._day_epoch != day:
            self._day_epoch = day
            self._daily_spent_micros = 0

    def check_and_reserve(
        self,
        *,
        estimated_cost_micros: int,
        request_budget_micros: Optional[int],
        daily_budget_micros: Optional[int],
        now: int,
    ) -> bool:
        """Returns True and reserves the spend, or False without side effects."""
        if (
            request_budget_micros is not None
            and estimated_cost_micros > request_budget_micros
        ):
            return False
        with self._lock:
            self._roll_day(now)
            if daily_budget_micros is not None:
                if (
                    self._daily_spent_micros + estimated_cost_micros
                    > daily_budget_micros
                ):
                    return False
            self._daily_spent_micros += estimated_cost_micros
            return True


def _validate_bind_host(host: str) -> str:
    # TitanRoutingConfig already enforces the private-network invariant;
    # this is a defense-in-depth re-check at the point a socket is actually
    # opened, matching hermes_cli.prime.server's own belt-and-suspenders
    # validation of a value that was already validated upstream.
    if not host or not host.strip():
        raise OmniRouteServerConfigError("OmniRoute bind host must not be blank")
    if host in ("0.0.0.0", "::"):
        raise OmniRouteServerConfigError(
            "OmniRoute must not bind to a wildcard address"
        )
    return host


class OmniRouteRequestHandler(BaseHTTPRequestHandler):
    server_version = "OmniRoute/1"
    config: TitanRoutingConfig
    upstreams: dict
    evidence_store: Optional[PrimeEvidenceStore]
    budget_tracker: BudgetTracker
    clock: Callable[[], int]
    hermes_router_reachable: Callable[[], bool]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        supplied = header[len("Bearer ") :].strip()
        return hmac.compare_digest(supplied, self.config.omniroute_auth_token)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return None
        max_bytes = self.config.max_request_bytes or MAX_REQUEST_BYTES
        if length <= 0 or length > max_bytes:
            return None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _correlation_id(self, body: dict) -> str:
        header = self.headers.get("X-Correlation-Id", "").strip()
        if header:
            return header[:128]
        from_body = body.get("correlation_id")
        if isinstance(from_body, str) and from_body.strip():
            return from_body.strip()[:128]
        return f"omniroute-{uuid.uuid4().hex}"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            snapshot = build_health_snapshot(
                omniroute_enabled=self.config.omniroute_enabled,
                titan_ollama_enabled=self.config.titan_ollama_enabled,
                freellmapi_enabled=self.config.freellmapi_enabled,
                offline_local_only=self.config.offline_local_only,
                titan_ollama_circuit=(
                    self.upstreams["titan_ollama"].circuit_breaker
                    if "titan_ollama" in self.upstreams
                    else None
                ),
                freellmapi_circuit=(
                    self.upstreams["freellmapi"].circuit_breaker
                    if "freellmapi" in self.upstreams
                    else None
                ),
                hermes_router_reachable=self.hermes_router_reachable(),
                now=self.clock(),
            )
            self._send_json(
                200,
                {
                    "operational_status": snapshot.operational_status.value,
                    "hermes_router": snapshot.hermes_router.value,
                    "omniroute": snapshot.omniroute.value,
                    "titan_ollama": snapshot.titan_ollama.value,
                    "freellmapi": snapshot.freellmapi.value,
                    "offline_local_only": snapshot.offline_local_only,
                    "internet_dependent_degradation": snapshot.internet_dependent_degradation,
                },
            )
            return

        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        if self.path == "/v1/models":
            models = sorted(self.config.alias_routes.keys())
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [{"id": alias, "object": "model"} for alias in models],
                },
            )
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": "not_found"})
            return

        body = self._read_json()
        if body is None:
            self._send_json(400, {"error": "invalid_json_body"})
            return

        correlation_id = self._correlation_id(body)
        alias = body.get("model")
        started = time.monotonic()
        now = self.clock()

        if not isinstance(alias, str) or not alias.strip():
            self._record_and_respond(
                status_code=422,
                correlation_id=correlation_id,
                requested_capability="",
                route_status=RouteStatus.POLICY_REJECTED,
                reason="model_field_missing_or_invalid",
                policy_rejected=True,
                latency_ms=_elapsed_ms(started),
                now=now,
                error_payload={
                    "error": "invalid_request",
                    "detail": "'model' must name a governed alias",
                },
            )
            return

        resolution = self.config.resolve_alias_detailed(alias)
        if not resolution.permitted:
            self._record_and_respond(
                status_code=403,
                correlation_id=correlation_id,
                requested_capability=alias,
                route_status=RouteStatus.POLICY_REJECTED,
                reason=resolution.reason,
                policy_rejected=True,
                latency_ms=_elapsed_ms(started),
                now=now,
                error_payload={"error": "policy_rejected", "reason": resolution.reason},
            )
            return

        provider_id, model = resolution.provider_id, resolution.model
        assert provider_id is not None and model is not None

        estimated_cost_micros = 0  # both governed providers are free today
        if not self.budget_tracker.check_and_reserve(
            estimated_cost_micros=estimated_cost_micros,
            request_budget_micros=self.config.request_budget_micros,
            daily_budget_micros=self.config.daily_budget_micros,
            now=now,
        ):
            self._record_and_respond(
                status_code=402,
                correlation_id=correlation_id,
                requested_capability=alias,
                route_status=RouteStatus.BUDGET_REJECTED,
                reason="budget_exceeded",
                budget_rejected=True,
                selected_provider=provider_id,
                selected_model=model,
                is_local_route=(provider_id == "titan_ollama"),
                latency_ms=_elapsed_ms(started),
                now=now,
                error_payload={"error": "budget_rejected"},
            )
            return

        messages = body.get("messages")
        input_text = _extract_last_user_message(messages)
        if input_text is None:
            self._record_and_respond(
                status_code=422,
                correlation_id=correlation_id,
                requested_capability=alias,
                route_status=RouteStatus.POLICY_REJECTED,
                reason="messages_field_missing_or_invalid",
                policy_rejected=True,
                selected_provider=provider_id,
                selected_model=model,
                is_local_route=(provider_id == "titan_ollama"),
                latency_ms=_elapsed_ms(started),
                now=now,
                error_payload={
                    "error": "invalid_request",
                    "detail": "'messages' must contain a user message",
                },
            )
            return

        upstream = self.upstreams.get(provider_id)
        if upstream is None:
            self._record_and_respond(
                status_code=503,
                correlation_id=correlation_id,
                requested_capability=alias,
                route_status=RouteStatus.POLICY_REJECTED,
                reason="upstream_not_configured",
                policy_rejected=True,
                selected_provider=provider_id,
                selected_model=model,
                is_local_route=(provider_id == "titan_ollama"),
                latency_ms=_elapsed_ms(started),
                now=now,
                error_payload={"error": "provider_unavailable"},
            )
            return

        timeout_seconds = (
            self.config.provider_timeout_ms.get(provider_id, 30_000) / 1_000
        )
        outcome = upstream.generate(
            model=model, input_text=input_text, timeout_seconds=timeout_seconds
        )

        if not outcome.succeeded:
            timed_out = (
                "timed out" in (outcome.error or "").lower()
                or "unreachable" in (outcome.error or "").lower()
            )
            self._record_and_respond(
                status_code=504 if timed_out else 502,
                correlation_id=correlation_id,
                requested_capability=alias,
                route_status=RouteStatus.TIMED_OUT if timed_out else RouteStatus.FAILED,
                reason="upstream_failure",
                provider_error=outcome.error,
                timeout_occurred=timed_out,
                selected_provider=provider_id,
                selected_model=model,
                is_local_route=(provider_id == "titan_ollama"),
                latency_ms=outcome.latency_ms or _elapsed_ms(started),
                now=now,
                error_payload={"error": "upstream_failed", "detail": outcome.error},
            )
            return

        self._record_and_respond(
            status_code=200,
            correlation_id=correlation_id,
            requested_capability=alias,
            route_status=RouteStatus.SUCCEEDED,
            reason="routed_by_priority",
            selected_provider=provider_id,
            selected_model=model,
            is_local_route=(provider_id == "titan_ollama"),
            latency_ms=outcome.latency_ms or _elapsed_ms(started),
            now=now,
            success_payload={
                "id": f"omniroute-{correlation_id}",
                "object": "chat.completion",
                "model": alias,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": outcome.output_text,
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def _record_and_respond(
        self,
        *,
        status_code: int,
        correlation_id: str,
        requested_capability: str,
        route_status: RouteStatus,
        reason: str,
        latency_ms: int,
        now: int,
        selected_provider: Optional[str] = None,
        selected_model: Optional[str] = None,
        is_local_route: Optional[bool] = None,
        policy_rejected: bool = False,
        budget_rejected: bool = False,
        timeout_occurred: bool = False,
        provider_error: Optional[str] = None,
        success_payload: Optional[dict] = None,
        error_payload: Optional[dict] = None,
    ) -> None:
        evidence = RouteDecisionEvidence(
            correlation_id=correlation_id,
            requested_capability=requested_capability or "unknown",
            selected_provider=selected_provider,
            selected_model=selected_model,
            is_local_route=is_local_route,
            reason=reason,
            timeout_occurred=timeout_occurred,
            provider_error=(provider_error[:256] if provider_error else None),
            policy_rejected=policy_rejected,
            budget_rejected=budget_rejected,
            status=route_status,
            latency_ms=max(0, latency_ms),
            observed_at=now,
        )
        if self.evidence_store is not None:
            try:
                record = build_route_decision_evidence_record(
                    evidence, producer_identity_id="titan-omniroute"
                )
                self.evidence_store.append(record)
            except Exception:  # noqa: BLE001 - evidence recording must never block a response
                logger.exception("failed to append OmniRoute route-decision evidence")

        payload = (
            success_payload if success_payload is not None else (error_payload or {})
        )
        payload.setdefault("correlation_id", correlation_id)
        self._send_json(status_code, payload)


def _extract_last_user_message(messages: object) -> Optional[str]:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def _elapsed_ms(started_monotonic: float) -> int:
    return int((time.monotonic() - started_monotonic) * 1_000)


def build_omniroute_http_server(
    *,
    config: TitanRoutingConfig,
    upstreams: dict,
    evidence_store: Optional[PrimeEvidenceStore] = None,
    budget_tracker: Optional[BudgetTracker] = None,
    clock: Optional[Callable[[], int]] = None,
    hermes_router_reachable: Optional[Callable[[], bool]] = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) the OmniRoute HTTP server.

    ``config`` must already be a validated :class:`TitanRoutingConfig` — its
    ``__post_init__`` has already run :func:`validate_no_mac_dependency` and
    every other invariant, so by the time this function is called there is
    no remaining path for a Mac-dependent or malformed configuration to
    reach a bound socket.
    """
    _validate_bind_host(config.bind_host)

    handler = type(
        "BoundOmniRouteRequestHandler",
        (OmniRouteRequestHandler,),
        {
            "config": config,
            "upstreams": upstreams,
            "evidence_store": evidence_store,
            "budget_tracker": budget_tracker or BudgetTracker(),
            "clock": staticmethod(clock or (lambda: int(time.time()))),
            "hermes_router_reachable": staticmethod(
                hermes_router_reachable or (lambda: True)
            ),
        },
    )
    return ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
