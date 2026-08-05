"""Minimal, stdlib-only HTTP control-plane server for Prime.

Fleet Unification live-runtime work. Per
``docs/architecture/FLEET_UNIFICATION_STAGES_2_9.md`` §8.2/§9, no real
network transport for node registration or heartbeat existed anywhere in
this repository — every Stage 2 decision module operates purely on
caller-supplied, in-process objects. This module is that transport: a
small, dependency-free HTTP server (stdlib ``http.server`` only — no new
third-party dependency) that lets Titan and Mac reach Prime over the
Tailscale private fleet network and register/heartbeat for real.

Security model: this server assumes the network-layer trust boundary is
Tailscale itself (only tailnet members can route to the bind address at
all) — the same assumption ``deploy/hermes-link/mac-coordinator.json.example``
already makes for Hermes Link. As defense in depth beyond that network
boundary, every non-health-check request must carry a shared bearer token
(``Authorization: Bearer <token>``) compared with ``hmac.compare_digest`` to
avoid timing side channels. The server refuses to start bound to a wildcard
or blank host — an operator must supply the node's actual Tailscale
hostname/IP, never ``0.0.0.0``.

This server has no route that grants execution, approval, or broker
authority — it only proxies to :class:`hermes_cli.prime.fleet_runtime.FleetRuntime`'s
already fail-closed ``register_node``/``ingest_heartbeat`` methods, so every
safety property those methods already have (unknown/duplicate/revoked/
malformed node rejection, stale-heartbeat detection) applies unchanged here.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from pydantic import ValidationError

from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetRegistrationOutcome
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.heartbeat import HeartbeatOutcome, HeartbeatSubmission

logger = logging.getLogger("hermes.prime.server")

MAX_REQUEST_BYTES = 65_536


class PrimeServerConfigError(ValueError):
    """Prime HTTP server configuration is invalid."""


def _validate_bind_host(host: str) -> str:
    if not host or not host.strip():
        raise PrimeServerConfigError(
            "Prime server bind host must be set explicitly (the node's Tailscale "
            "hostname or IP) — it cannot be blank"
        )
    if host in ("0.0.0.0", "::"):
        raise PrimeServerConfigError(
            "Prime server must not bind to a wildcard address; bind to the node's "
            "actual Tailscale interface address instead"
        )
    return host


def _validate_auth_token(token: str) -> str:
    if not token or len(token) < 16:
        raise PrimeServerConfigError(
            "Prime server auth token must be set and at least 16 characters"
        )
    return token


class PrimeRequestHandler(BaseHTTPRequestHandler):
    """Base handler. Concrete request-serving state is bound via subclassing
    in :func:`build_prime_http_server` (stdlib ``http.server`` has no
    built-in dependency-injection story, so a small per-server subclass is
    the standard way to hand state to handler instances)."""

    server_version = "PrimeControlPlane/1"
    fleet_runtime: FleetRuntime
    auth_token: str
    clock: Callable[[], int]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        supplied = header[len("Bearer "):].strip()
        return hmac.compare_digest(supplied, self.auth_token)

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
        if length <= 0 or length > MAX_REQUEST_BYTES:
            return None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        if self.path == "/v1/fleet/health":
            self._send_json(200, {"status": "ok"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        if self.path == "/v1/fleet/nodes":
            nodes = self.fleet_runtime.registry.all()
            self._send_json(200, {"nodes": [n.model_dump(mode="json") for n in nodes]})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        body = self._read_json()
        if body is None:
            self._send_json(400, {"error": "invalid_json_body"})
            return
        now = self.clock()

        if self.path == "/v1/fleet/nodes/register":
            try:
                request = FleetNodeRegistrationRequest(**body)
            except ValidationError as error:
                self._send_json(422, {"error": "invalid_request", "detail": str(error)})
                return
            decision = self.fleet_runtime.register_node(request, now=now)
            status = 200 if decision.outcome != FleetRegistrationOutcome.REJECTED else 409
            self._send_json(status, decision.model_dump(mode="json"))
            return

        if self.path == "/v1/fleet/nodes/heartbeat":
            try:
                submission = HeartbeatSubmission(**body)
            except ValidationError as error:
                self._send_json(422, {"error": "invalid_request", "detail": str(error)})
                return
            result = self.fleet_runtime.ingest_heartbeat(submission, now=now)
            status = 200 if result.outcome == HeartbeatOutcome.ACCEPTED else 409
            self._send_json(status, result.model_dump(mode="json"))
            return

        self._send_json(404, {"error": "not_found"})


def build_prime_http_server(
    *,
    host: str,
    port: int,
    fleet_runtime: FleetRuntime,
    auth_token: str,
    clock: Optional[Callable[[], int]] = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) a Prime control-plane HTTP server.

    Raises :class:`PrimeServerConfigError` for an unsafe host or missing/weak
    auth token — this validation happens before a socket is ever opened.
    """
    _validate_bind_host(host)
    _validate_auth_token(auth_token)

    bound_clock = clock or (lambda: int(time.time()))

    handler = type(
        "BoundPrimeRequestHandler",
        (PrimeRequestHandler,),
        {
            "fleet_runtime": fleet_runtime,
            "auth_token": auth_token,
            "clock": staticmethod(bound_clock),
        },
    )
    return ThreadingHTTPServer((host, port), handler)
