from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from sigil.desktop_bridge.prime_fleet import prime_fleet_status, prime_sigil_route

AUTH_TOKEN = "test-token-0123456789abcdef"


class _FakePrimeHandler(BaseHTTPRequestHandler):
    nodes_response: ClassVar[dict] = {"nodes": []}
    certification_response: ClassVar[dict] = {"status": "unknown", "evidence_ref": None}
    route_response: ClassVar[dict] = {"ok": True, "outcome": "accepted"}
    route_status: int = 200

    def log_message(self, log_format, *args):
        pass

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {AUTH_TOKEN}"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        if self.path == "/v1/fleet/nodes":
            self._send_json(200, self.nodes_response)
            return
        if self.path == "/v1/fleet/certification":
            self._send_json(200, self.certification_response)
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        if self.path == "/v1/sigil/route":
            self._send_json(self.route_status, self.route_response)
            return
        self._send_json(404, {"error": "not_found"})


@pytest.fixture()
def fake_prime():
    _FakePrimeHandler.nodes_response = {"nodes": []}
    _FakePrimeHandler.certification_response = {"status": "unknown", "evidence_ref": None}
    _FakePrimeHandler.route_response = {"ok": True, "outcome": "accepted"}
    _FakePrimeHandler.route_status = 200

    server = HTTPServer(("127.0.0.1", 0), _FakePrimeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", _FakePrimeHandler
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_fleet_status_not_configured_when_env_missing() -> None:
    result = prime_fleet_status({})
    assert result["configured"] is False
    assert result["reachable"] is False
    assert result["nodes"] == []
    assert result["certification"] == {"status": "unknown", "evidence_ref": None}


def test_fleet_status_unreachable_when_prime_is_down() -> None:
    result = prime_fleet_status(
        {"HERMES_PRIME_BASE_URL": "http://127.0.0.1:1", "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN}
    )
    assert result["configured"] is True
    assert result["reachable"] is False
    assert result["nodes"] == []


def test_fleet_status_reports_real_nodes_and_certification(fake_prime) -> None:
    base_url, handler = fake_prime
    handler.nodes_response = {
        "nodes": [
            {"natural_key": "titan", "connection_state": "connected", "role": "titan"},
        ]
    }
    handler.certification_response = {"status": "certified", "evidence_ref": "evidence://x"}

    result = prime_fleet_status({"HERMES_PRIME_BASE_URL": base_url, "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN})
    assert result["configured"] is True
    assert result["reachable"] is True
    assert result["nodes"][0]["natural_key"] == "titan"
    assert result["certification"]["status"] == "certified"


def test_fleet_status_rejects_wrong_auth_token(fake_prime) -> None:
    base_url, _ = fake_prime
    result = prime_fleet_status({"HERMES_PRIME_BASE_URL": base_url, "HERMES_PRIME_AUTH_TOKEN": "wrong-token"})
    assert result["configured"] is True
    assert result["reachable"] is False


def test_sigil_route_not_configured_when_env_missing() -> None:
    result = prime_sigil_route({"operation": "advisory_financial_sentiment"}, {})
    assert result["ok"] is False
    assert result["error"] == "prime_not_configured"


def test_sigil_route_rejects_unsupported_operation(fake_prime) -> None:
    base_url, _ = fake_prime
    result = prime_sigil_route(
        {"operation": "not_a_real_operation"},
        {"HERMES_PRIME_BASE_URL": base_url, "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN},
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_request"


def test_sigil_route_returns_prime_response_verbatim(fake_prime) -> None:
    base_url, handler = fake_prime
    handler.route_response = {
        "ok": True,
        "outcome": "accepted",
        "advisory_output": {"routed_to": "titan", "model_alias": "sentiment"},
    }
    result = prime_sigil_route(
        {"operation": "advisory_financial_sentiment", "input_payload": {"symbol": "TEST"}},
        {"HERMES_PRIME_BASE_URL": base_url, "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN},
    )
    assert result["ok"] is True
    assert result["advisory_output"]["routed_to"] == "titan"


def test_sigil_route_surfaces_real_rejection(fake_prime) -> None:
    base_url, handler = fake_prime
    handler.route_status = 409
    handler.route_response = {"ok": False, "outcome": "rejected", "rejection_code": "service_not_admitted"}
    result = prime_sigil_route(
        {"operation": "advisory_financial_sentiment"},
        {"HERMES_PRIME_BASE_URL": base_url, "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN},
    )
    assert result["ok"] is False
    assert result["rejection_code"] == "service_not_admitted"


def test_sigil_route_unreachable_prime() -> None:
    result = prime_sigil_route(
        {"operation": "advisory_financial_sentiment"},
        {"HERMES_PRIME_BASE_URL": "http://127.0.0.1:1", "HERMES_PRIME_AUTH_TOKEN": AUTH_TOKEN},
    )
    assert result["ok"] is False
    assert result["error"] == "prime_unreachable"
