"""A minimal, disabled-by-default, loopback-only reference Buzz relay server.

Upstream identity (read-only diligence, 2026-08-05): the confirmed real Buzz
is ``block/buzz`` (Apache-2.0, Block Inc., https://github.com/block/buzz) --
"a workspace where humans and agents share the same rooms," a Rust monorepo
built on the Nostr protocol (NIP-01 wire format, NIP-42 auth), whose relay
component is literally named ``buzz-relay`` and exposes an HTTP bridge
including ``/events``. This is a strong, name-and-architecture-matched
identification, not a guess.

**This module is not a Nostr relay and does not claim protocol parity with
block/buzz.** Real compatibility would require implementing NIP-01/NIP-42
event verification (Schnorr signatures over secp256k1) -- a meaningfully
sized cryptography task that deserves its own dedicated implementation and
review, not a rushed addition here. Per explicit instruction, this run
preserves the existing HMAC-SHA256 verification in
``sigil.buzz_relay_adapter`` unchanged rather than downgrading or replacing
it.

What this module *is*: the minimum internal reference implementation needed
to exercise the existing, already-verified ``sigil.buzz_relay_adapter``
contract (``BuzzRelayEvent`` + ``verify_event_signature``) end-to-end over a
real HTTP server, so Hermes's own Buzz Relay code path can be integration-
tested without depending on deploying the full external Buzz product. It is
loopback-only, single-process, in-memory, and disabled by default.
"""

from __future__ import annotations

import ipaddress
import json
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from sigil.buzz_relay_adapter import (
    BuzzActorKind,
    BuzzActorRef,
    BuzzDeliveryState,
    BuzzEventKind,
    BuzzRelayConfig,
    BuzzRelayEvent,
    BuzzRelayValidationError,
    BuzzReplayWindow,
    BuzzSpaceRef,
    BuzzThreadRef,
    evaluate_relay_event,
    initial_replay_window,
)

BUZZ_REFERENCE_RELAY_SCHEMA_VERSION = 1
_MAX_REQUEST_BYTES = 262_144  # matches the ~256 KiB ingest bound noted for block/buzz's own bridge


class BuzzReferenceRelayError(ValueError):
    """A reference relay operation failed closed."""


@dataclass(frozen=True, slots=True)
class BuzzReferenceRelayConfig:
    enabled: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = 0  # 0 == OS-assigned ephemeral port; never a fixed public port by default
    signing_key: bytes = b""
    schema_version: int = BUZZ_REFERENCE_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != BUZZ_REFERENCE_RELAY_SCHEMA_VERSION:
            raise BuzzReferenceRelayError("unsupported reference relay config schema")
        try:
            address = ipaddress.ip_address(self.bind_host)
        except ValueError as error:
            raise BuzzReferenceRelayError("bind_host must be a literal IP address") from error
        if not (address.is_loopback):
            raise BuzzReferenceRelayError(
                "the reference relay may only bind to a loopback address"
            )
        if len(self.signing_key) < 32:
            raise BuzzReferenceRelayError("signing_key must be at least 32 bytes")


@dataclass
class _RelayState:
    window: BuzzReplayWindow = field(default_factory=initial_replay_window)
    accepted_events: list[BuzzRelayEvent] = field(default_factory=list)
    rejected_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _event_from_payload(payload: dict[str, Any]) -> BuzzRelayEvent:
    try:
        space = BuzzSpaceRef(**payload["space"])
        actor = BuzzActorRef(
            actor_id=payload["actor"]["actor_id"],
            display_name=payload["actor"]["display_name"],
            kind=BuzzActorKind(payload["actor"]["kind"]),
            organization_identity=payload["actor"]["organization_identity"],
        )
        thread = BuzzThreadRef(**payload["thread"])
        return BuzzRelayEvent(
            event_id=payload["event_id"],
            sequence=payload["sequence"],
            emitted_at=payload["emitted_at"],
            kind=BuzzEventKind(payload["kind"]),
            space=space,
            actor=actor,
            thread=thread,
            correlation_id=payload["correlation_id"],
            idempotency_key=payload["idempotency_key"],
            payload=payload["payload"],
            payload_digest=payload["payload_digest"],
            previous_event_digest=payload["previous_event_digest"],
            signature=payload["signature"],
        )
    except (KeyError, TypeError, ValueError, BuzzRelayValidationError) as error:
        raise BuzzReferenceRelayError(f"malformed event payload: {error}") from error


class _Handler(BaseHTTPRequestHandler):
    server: "BuzzReferenceRelayServer"  # type: ignore[assignment]

    def log_message(self, *args: object) -> None:  # silence default request logging
        return

    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/events":
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length > _MAX_REQUEST_BYTES:
            self._json(413, {"error": "event payload too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            event = _event_from_payload(payload)
        except (json.JSONDecodeError, BuzzReferenceRelayError) as error:
            self._json(400, {"error": str(error)})
            return

        state = self.server.state
        config = self.server.relay_config
        with state.lock:
            decision = evaluate_relay_event(
                BuzzRelayConfig(enabled=True),
                event,
                state.window,
                age_seconds=0,
                signing_key=config.signing_key,
            )
            if decision.state is BuzzDeliveryState.ACCEPTED:
                state.window = decision.next_window
                state.accepted_events.append(event)
            else:
                state.rejected_count += 1

        self._json(
            200 if decision.state is BuzzDeliveryState.ACCEPTED else 409,
            {"state": decision.state.value, "accepted": decision.accepted, "reason": decision.reason},
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/events":
            self._json(404, {"error": "not found"})
            return

        state = self.server.state
        with state.lock:
            events = [
                {**asdict(event), "kind": event.kind.value, "space": asdict(event.space)}
                for event in state.accepted_events
            ]
        self._json(200, {"events": events, "rejected_count": state.rejected_count})


class BuzzReferenceRelayServer:
    """Owns a real, background-threaded loopback HTTP server for the reference relay."""

    def __init__(self, config: BuzzReferenceRelayConfig) -> None:
        if not config.enabled:
            raise BuzzReferenceRelayError("reference relay is disabled by policy")
        self._config = config
        self._state = _RelayState()
        self._httpd = HTTPServer((config.bind_host, config.bind_port), _Handler)
        self._httpd.state = self._state  # type: ignore[attr-defined]
        self._httpd.relay_config = config  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def accepted_event_count(self) -> int:
        return len(self._state.accepted_events)

    @property
    def rejected_count(self) -> int:
        return self._state.rejected_count

    def start(self) -> None:
        if self._thread is not None:
            raise BuzzReferenceRelayError("reference relay is already running")
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd.server_close()
