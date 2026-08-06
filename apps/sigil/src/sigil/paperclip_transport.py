"""Real, disabled-by-default HTTP transport for a self-hosted Paperclip instance.

Hermes add-on continuation run. Upstream identity (read-only diligence,
2026-08-05): the confirmed official Paperclip is ``paperclipai/paperclip``
(MIT, https://github.com/paperclipai/paperclip), whose own
``docs/api/{overview,authentication,agents,issues}.md`` this module's request
shapes and endpoints are taken from directly. It is a pnpm monorepo
(PostgreSQL-backed server + web UI + CLI) meant to be deployed by the
operator (its own ``Dockerfile``/``docker/`` directory is the documented
install path) -- this module is deliberately only an API *client* against a
Paperclip instance the operator has already deployed and pointed this
config at. **This repository never installs, runs, or deploys the Paperclip
server itself**; doing so is an infrastructure decision outside this
codebase's scope, consistent with every other "governed adapter, not a
second runtime" boundary already established here (see
``docs/architecture/OLLAMA_ROUTING_BOUNDARY.md`` for the parallel pattern).

Security posture reviewed from the public repository (read-only, no code
executed): PostgreSQL + ``better-auth`` backed, bearer-token API
authentication (agent API keys or short-lived run JWTs), company-scoped
authorization enforced server-side, standard documented HTTP error codes.
No request in this module ever grants Paperclip authority over this host --
it is exclusively an outbound HTTP client using a bearer token the operator
supplies; no shell, filesystem, or credential-return capability of any kind
is exposed to a Paperclip response.

Every function requires ``config.enabled`` and a real, operator-supplied
:class:`PaperclipCredential`. Nothing here is ever called with a fabricated
or placeholder credential.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

PAPERCLIP_TRANSPORT_SCHEMA_VERSION = 1

_MAX_RESPONSE_BYTES = 2_000_000
_USER_AGENT = "HermesPaperclipAdapter/1.0 (+https://github.com/firecattechllc/hermes-agent)"

# Documented in docs/api/overview.md: transient failure, safe to record and
# move on without retrying automatically (Paperclip's own guidance for 500;
# for 409 the docs are explicit: "Do not retry.").
_NO_AUTO_RETRY_CODES = frozenset({409, 422})


class PaperclipTransportError(RuntimeError):
    """A Paperclip API call failed closed."""


class PaperclipHTTPError(PaperclipTransportError):
    """The Paperclip API returned a documented non-2xx error code."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Paperclip API returned HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.retryable = status not in _NO_AUTO_RETRY_CODES and status >= 500


@dataclass(frozen=True, slots=True)
class PaperclipTransportConfig:
    enabled: bool = False
    base_url: str = "http://localhost:3100"
    timeout_seconds: float = 15.0
    max_response_bytes: int = _MAX_RESPONSE_BYTES
    schema_version: int = PAPERCLIP_TRANSPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PAPERCLIP_TRANSPORT_SCHEMA_VERSION:
            raise PaperclipTransportError("unsupported Paperclip transport config schema")
        if not 0 < self.timeout_seconds <= 60:
            raise PaperclipTransportError("timeout is outside bounds")
        if not 1_000 <= self.max_response_bytes <= 20_000_000:
            raise PaperclipTransportError("response size cap is outside bounds")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PaperclipTransportError("base_url must be a valid http(s) URL")

    @property
    def can_execute_shell(self) -> bool:
        return False

    @property
    def can_access_local_filesystem(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PaperclipCredential:
    """A bearer token supplied by the operator at call time.

    Deliberately not a field on :class:`PaperclipTransportConfig`: the
    config is a plain dataclass that could end up in logs/evidence
    payloads, so the credential is threaded through call arguments only,
    mirroring the pattern used for ``signing_key`` in
    ``sigil.buzz_relay_adapter.verify_event_signature``.
    """

    token: str

    def __post_init__(self) -> None:
        if not self.token or not self.token.strip():
            raise PaperclipTransportError("Paperclip credential token cannot be blank")

    def __repr__(self) -> str:  # never leak the token via repr/logging
        return "PaperclipCredential(token=***redacted***)"


def _require_enabled(config: PaperclipTransportConfig) -> None:
    if not config.enabled:
        raise PaperclipTransportError("Paperclip transport is disabled by policy")


def _request(
    config: PaperclipTransportConfig,
    credential: PaperclipCredential,
    method: Literal["GET", "POST", "PATCH"],
    path: str,
    *,
    body: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """One bounded, authenticated request against ``config.base_url + path``.

    Real HTTP transport (not a placeholder): opens a real socket, sends a
    real bearer-authenticated request, and parses a real JSON response.
    Never retries automatically -- Paperclip's own API docs say 409/422
    must not be retried, and this module leaves all retry policy to the
    caller for every other code too.
    """

    _require_enabled(config)
    if not path.startswith("/api/"):
        raise PaperclipTransportError("path must be under /api/")

    parsed = urlsplit(config.base_url)
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    full_path = f"{parsed.path.rstrip('/')}{path}"

    payload_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
    headers = {
        "Authorization": f"Bearer {credential.token}",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    if run_id is not None:
        headers["X-Paperclip-Run-Id"] = run_id

    try:
        raw_sock = socket.create_connection((hostname, port), timeout=config.timeout_seconds)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            # Local/self-hosted deployments commonly run without a public
            # CA-signed cert (docs/deploy/tailscale-private-access.md);
            # loopback/private targets are allowed to skip hostname
            # verification, but a public-looking hostname is still verified.
            try:
                address = ipaddress.ip_address(hostname)
                is_private = address.is_private or address.is_loopback
            except ValueError:
                is_private = hostname in {"localhost"} or hostname.endswith(".ts.net")
            if is_private:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            wrapped = context.wrap_socket(raw_sock, server_hostname=hostname)
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                hostname, timeout=config.timeout_seconds
            )
            connection.sock = wrapped
        else:
            connection = http.client.HTTPConnection(hostname, timeout=config.timeout_seconds)
            connection.sock = raw_sock

        connection.request(method, full_path, body=payload_bytes or None, headers=headers)
        response = connection.getresponse()
        response_body = response.read(config.max_response_bytes)
        status = response.status
        connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
        raise PaperclipTransportError(f"Paperclip request failed: {error}") from error

    try:
        parsed_body = json.loads(response_body.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        parsed_body = {"raw": response_body.decode("utf-8", errors="replace")[:2_000]}

    if not 200 <= status < 300:
        message = parsed_body.get("error", "unknown error") if isinstance(parsed_body, dict) else "unknown error"
        raise PaperclipHTTPError(status, str(message))

    return parsed_body if isinstance(parsed_body, dict) else {"result": parsed_body}


def get_current_agent_identity(
    config: PaperclipTransportConfig, credential: PaperclipCredential
) -> dict[str, Any]:
    """``GET /api/agents/me`` -- verify identity and retrieve budget/chain-of-command."""

    return _request(config, credential, "GET", "/api/agents/me")


def list_issues(
    config: PaperclipTransportConfig,
    credential: PaperclipCredential,
    company_id: str,
    *,
    status: str | None = None,
    assignee_agent_id: str | None = None,
) -> dict[str, Any]:
    """``GET /api/companies/{companyId}/issues`` (status retrieval, read-only)."""

    if not company_id.strip():
        raise PaperclipTransportError("company_id is required")

    path = f"/api/companies/{company_id}/issues"
    query: list[str] = []
    if status:
        query.append(f"status={status}")
    if assignee_agent_id:
        query.append(f"assigneeAgentId={assignee_agent_id}")
    if query:
        path = f"{path}?{'&'.join(query)}"

    return _request(config, credential, "GET", path)


def get_issue(
    config: PaperclipTransportConfig, credential: PaperclipCredential, issue_id: str
) -> dict[str, Any]:
    """``GET /api/issues/{issueId}`` (status retrieval, read-only)."""

    if not issue_id.strip():
        raise PaperclipTransportError("issue_id is required")

    return _request(config, credential, "GET", f"/api/issues/{issue_id}")


def update_issue_status(
    config: PaperclipTransportConfig,
    credential: PaperclipCredential,
    issue_id: str,
    *,
    status: str,
    comment: str,
    run_id: str,
) -> dict[str, Any]:
    """``PATCH /api/issues/{issueId}`` with a decision comment (job dispatch: status update only).

    Per Paperclip's own API docs, a status-changing decision must carry its
    comment in the *same* PATCH call (a prior separate comment does not
    satisfy their stage-decision guard), so both are required together
    here rather than offered as two calls that could be split unsafely.
    """

    if not issue_id.strip():
        raise PaperclipTransportError("issue_id is required")
    if not comment.strip():
        raise PaperclipTransportError("a decision comment is required for a status update")
    if not run_id.strip():
        raise PaperclipTransportError("run_id is required for audit trail (X-Paperclip-Run-Id)")

    return _request(
        config,
        credential,
        "PATCH",
        f"/api/issues/{issue_id}",
        body={"status": status, "comment": comment},
        run_id=run_id,
    )
