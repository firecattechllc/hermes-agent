"""Sigil 3.7.0 governed-fleet-release certification.

Proves the specific safety claims Sigil 3.7.0 makes about its new surface
area (the Prime fleet bridge added this release) hold for real, not just
that the underlying hermes_cli.prime modules pass their own tests in
isolation. Every check here drives real code -- no hardcoded pass.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

from sigil.desktop_bridge.prime_fleet import prime_fleet_status, prime_sigil_route
from sigil.desktop_bridge.runner import SUPPORTED_COMMANDS, handle_request

AUTH_TOKEN = "cert-token-0123456789abcdef"


def test_prime_fleet_commands_are_allow_listed() -> None:
    """The new Prime bridge commands must be explicit, closed-allowlist
    members -- not reachable through any other path."""
    assert "prime_fleet_status" in SUPPORTED_COMMANDS
    assert "prime_sigil_route" in SUPPORTED_COMMANDS


def test_fleet_status_never_reports_configured_prime_as_reachable_when_down() -> None:
    """Unknown/unreachable must never be represented as healthy."""
    result = prime_fleet_status(
        {"HERMES_PRIME_BASE_URL": "http://127.0.0.1:1", "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN}
    )
    assert result["reachable"] is False
    assert result["certification"]["status"] == "unknown"
    assert result["nodes"] == []


def test_sigil_route_rejects_every_operation_outside_the_closed_allowlist() -> None:
    for bogus_operation in ("execute_live_order", "submit_broker_order", "", "advisory_"):
        result = prime_sigil_route({"operation": bogus_operation}, {})
        assert result["ok"] is False


class _CertificationPrimeHandler(BaseHTTPRequestHandler):
    """A minimal fake Prime that always reports a rejection, so this test
    proves the bridge surfaces a real rejection rather than ever
    synthesizing an accepted route locally."""

    route_response: ClassVar[dict] = {
        "ok": False,
        "outcome": "rejected",
        "rejection_code": "service_not_admitted",
    }

    def log_message(self, log_format, *args):
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
            self._send_json(401, {"error": "unauthorized"})
            return
        self._send_json(409, self.route_response)


def test_bridge_never_locally_fabricates_an_accepted_route() -> None:
    server = HTTPServer(("127.0.0.1", 0), _CertificationPrimeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        response = handle_request(
            {
                "command": "prime_sigil_route",
                "payload": {"operation": "advisory_financial_sentiment"},
            }
        )
        # No env configured in this process -> honest not-configured, never
        # a fabricated accept just because a command was reachable.
        assert response["ok"] is True
        assert response["result"]["ok"] is False
        assert response["result"]["error"] == "prime_not_configured"

        # And with a real (rejecting) Prime configured via prime_sigil_route
        # directly, the rejection is surfaced verbatim.
        direct_result = prime_sigil_route(
            {"operation": "advisory_financial_sentiment"},
            {"HERMES_PRIME_BASE_URL": f"http://127.0.0.1:{port}", "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN},
        )
        assert direct_result["ok"] is False
        assert direct_result["rejection_code"] == "service_not_admitted"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_backend_status_still_advertises_paper_only_no_execution_no_broker() -> None:
    """The pre-existing backend_status() invariants must survive unchanged
    by the 3.7.0 additions."""
    from sigil.desktop_bridge.runner import backend_status

    status = backend_status()
    assert status["environment"] == "paper"
    assert status["simulation"] is True
    assert status["execution_authorized"] is False
    assert status["broker_submission_available"] is False
