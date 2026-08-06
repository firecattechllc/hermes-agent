from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sigil.paperclip_transport import (
    PaperclipCredential,
    PaperclipHTTPError,
    PaperclipTransportConfig,
    PaperclipTransportError,
    get_current_agent_identity,
    get_issue,
    list_issues,
    update_issue_status,
)

VALID_TOKEN = "test-bearer-token-value"


class _FakePaperclipHandler(BaseHTTPRequestHandler):
    """A minimal in-process stand-in for a real Paperclip API server.

    Exercises the real transport module end-to-end over a real loopback
    socket -- not a mock of the transport itself -- so these tests prove
    the request/response wire format, not just that a function was called.
    """

    def log_message(self, *args: object) -> None:  # silence test output
        return

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {VALID_TOKEN}"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": "API key missing or invalid"})
            return
        if self.path == "/api/agents/me":
            self._send_json(200, {"id": "agent-42", "name": "BackendEngineer", "role": "engineer"})
            return
        if self.path.startswith("/api/companies/company-1/issues"):
            self._send_json(200, [{"id": "issue-1", "title": "Test issue", "status": "todo"}])
            return
        if self.path == "/api/issues/issue-1":
            self._send_json(200, {"id": "issue-1", "title": "Test issue", "status": "todo"})
            return
        if self.path == "/api/issues/conflicted":
            self._send_json(409, {"error": "Another agent owns the task"})
            return
        self._send_json(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": "API key missing or invalid"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/issues/issue-1":
            if "X-Paperclip-Run-Id" not in self.headers:
                self._send_json(400, {"error": "missing run id"})
                return
            self._send_json(200, {"id": "issue-1", "status": body.get("status"), "comment": body.get("comment")})
            return
        self._send_json(404, {"error": "not found"})


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _FakePaperclipHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def config(server) -> PaperclipTransportConfig:
    port = server.server_address[1]
    return PaperclipTransportConfig(enabled=True, base_url=f"http://127.0.0.1:{port}")


def credential() -> PaperclipCredential:
    return PaperclipCredential(token=VALID_TOKEN)


def test_config_rejects_out_of_bounds_timeout() -> None:
    with pytest.raises(PaperclipTransportError, match="timeout"):
        PaperclipTransportConfig(timeout_seconds=0)


def test_config_rejects_malformed_base_url() -> None:
    with pytest.raises(PaperclipTransportError, match="base_url"):
        PaperclipTransportConfig(base_url="not-a-url")


def test_credential_rejects_blank_token() -> None:
    with pytest.raises(PaperclipTransportError, match="blank"):
        PaperclipCredential(token="")


def test_credential_repr_never_leaks_token() -> None:
    cred = PaperclipCredential(token="super-secret-token-value")

    assert "super-secret-token-value" not in repr(cred)
    assert "redacted" in repr(cred)


def test_disabled_config_rejects_every_call(server) -> None:
    disabled = PaperclipTransportConfig(enabled=False, base_url=config(server).base_url)

    with pytest.raises(PaperclipTransportError, match="disabled"):
        get_current_agent_identity(disabled, credential())


def test_get_current_agent_identity_real_round_trip(server) -> None:
    result = get_current_agent_identity(config(server), credential())

    assert result["id"] == "agent-42"
    assert result["role"] == "engineer"


def test_invalid_credential_surfaces_401(server) -> None:
    bad_credential = PaperclipCredential(token="wrong-token")

    with pytest.raises(PaperclipHTTPError) as excinfo:
        get_current_agent_identity(config(server), bad_credential)

    assert excinfo.value.status == 401


def test_list_issues_real_round_trip(server) -> None:
    result = list_issues(config(server), credential(), "company-1", status="todo")

    assert result["result"] == [{"id": "issue-1", "title": "Test issue", "status": "todo"}]


def test_list_issues_requires_company_id(server) -> None:
    with pytest.raises(PaperclipTransportError, match="company_id"):
        list_issues(config(server), credential(), "")


def test_get_issue_real_round_trip(server) -> None:
    result = get_issue(config(server), credential(), "issue-1")

    assert result["title"] == "Test issue"


def test_conflict_status_is_marked_non_retryable(server) -> None:
    with pytest.raises(PaperclipHTTPError) as excinfo:
        get_issue(config(server), credential(), "conflicted")

    assert excinfo.value.status == 409
    assert excinfo.value.retryable is False


def test_update_issue_status_requires_comment(server) -> None:
    with pytest.raises(PaperclipTransportError, match="comment"):
        update_issue_status(
            config(server), credential(), "issue-1", status="done", comment="", run_id="run-1"
        )


def test_update_issue_status_requires_run_id(server) -> None:
    with pytest.raises(PaperclipTransportError, match="run_id"):
        update_issue_status(
            config(server), credential(), "issue-1", status="done", comment="Done.", run_id=""
        )


def test_update_issue_status_real_round_trip_carries_comment_and_run_id(server) -> None:
    result = update_issue_status(
        config(server),
        credential(),
        "issue-1",
        status="done",
        comment="Implemented caching with 90% hit rate.",
        run_id="run-abc123",
    )

    assert result["status"] == "done"
    assert result["comment"] == "Implemented caching with 90% hit rate."


def test_transport_config_never_grants_local_execution_authority() -> None:
    cfg = PaperclipTransportConfig(enabled=True)

    assert cfg.can_execute_shell is False
    assert cfg.can_access_local_filesystem is False
