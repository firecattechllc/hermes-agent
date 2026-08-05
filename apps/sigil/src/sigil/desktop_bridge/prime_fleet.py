"""Real, governed visibility into the Hermes Prime fleet control plane.

Sigil deliberately has no import dependency on ``hermes_cli`` (a separate
top-level package, not staged into the packaged Electron build) -- this
module talks to Prime purely over its HTTP contract
(``hermes_cli.prime.server``), using the same bearer-token auth model, via
stdlib ``urllib`` only. Prime's base URL and shared auth token are read from
the environment (``HERMES_PRIME_BASE_URL`` / ``HERMES_PRIME_AUTH_TOKEN``),
the same variables the Titan/Mac worker services use.

Every function here fails closed and honest: if Prime is not configured, is
unreachable, or returns malformed data, the result says so explicitly
(``configured: False`` / ``reachable: False`` / a real HTTP status code) --
it never fabricates a healthy node, a certified fleet, or a successful route
that did not actually happen.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 1_000_000


def _prime_config(environment: dict[str, str] | None = None) -> tuple[str, str] | None:
    source = os.environ if environment is None else environment
    base_url = source.get("HERMES_PRIME_BASE_URL", "").strip()
    auth_token = source.get("HERMES_PRIME_AUTH_TOKEN", "").strip()
    if not base_url or not auth_token:
        return None
    return base_url.rstrip("/"), auth_token


def _request(
    base_url: str,
    auth_token: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int | None, Any]:
    """Return ``(status_code, parsed_body)``. ``status_code`` is ``None`` only
    for a network-level failure (unreachable, timeout, DNS) -- a real HTTP
    error response still returns its actual status code and body."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {auth_token}",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES)
            return response.status, json.loads(raw.decode("utf-8"))
    except HTTPError as error:
        try:
            body = json.loads(error.read(MAX_RESPONSE_BYTES).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            body = {"error": "unreadable_error_body"}
        return error.code, body
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, None


def prime_fleet_status(environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Real node inventory + certification status from a live Prime, or an
    honest not-configured/unreachable result -- never a fabricated healthy
    fleet."""
    config = _prime_config(environment)
    if config is None:
        return {
            "configured": False,
            "reachable": False,
            "base_url": None,
            "nodes": [],
            "certification": {"status": "unknown", "evidence_ref": None},
        }
    base_url, auth_token = config

    nodes_status, nodes_body = _request(base_url, auth_token, "/v1/fleet/nodes")
    cert_status, cert_body = _request(base_url, auth_token, "/v1/fleet/certification")

    reachable = nodes_status == 200 and cert_status == 200
    nodes = nodes_body.get("nodes", []) if reachable and isinstance(nodes_body, dict) else []
    certification = (
        cert_body
        if reachable and isinstance(cert_body, dict)
        else {"status": "unknown", "evidence_ref": None}
    )

    return {
        "configured": True,
        "reachable": reachable,
        "base_url": base_url,
        "nodes": nodes,
        "certification": certification,
        "http_status": {"nodes": nodes_status, "certification": cert_status},
    }


SUPPORTED_SIGIL_OPERATIONS = frozenset(
    {
        "advisory_valuation",
        "advisory_risk_assessment",
        "advisory_portfolio_construction",
        "advisory_financial_sentiment",
        "advisory_research_summary",
    }
)


def prime_sigil_route(
    payload: dict[str, Any], environment: dict[str, str] | None = None
) -> dict[str, Any]:
    """Route one advisory request through Prime's governed
    ``POST /v1/sigil/route`` contract. Returns Prime's real response verbatim
    (accepted or rejected, with its real rejection_code) or an honest
    not-configured/unreachable error -- never a locally fabricated result."""
    config = _prime_config(environment)
    if config is None:
        return {"ok": False, "error": "prime_not_configured", "message": "Prime is not configured"}

    operation = payload.get("operation")
    if not isinstance(operation, str) or operation not in SUPPORTED_SIGIL_OPERATIONS:
        return {
            "ok": False,
            "error": "invalid_request",
            "message": f"operation must be one of {sorted(SUPPORTED_SIGIL_OPERATIONS)}",
        }

    base_url, auth_token = config
    request_payload = {
        "operation": operation,
        "input_payload": payload.get("input_payload") if isinstance(payload.get("input_payload"), dict) else {},
        "timeout_seconds": 90,
    }
    status, body = _request(
        base_url,
        auth_token,
        "/v1/sigil/route",
        method="POST",
        payload=request_payload,
        timeout_seconds=90.0,
    )
    if status is None:
        return {"ok": False, "error": "prime_unreachable", "message": f"Prime at {base_url} did not respond"}
    if not isinstance(body, dict):
        return {"ok": False, "error": "malformed_response", "message": "Prime returned a non-JSON-object body"}
    return body
