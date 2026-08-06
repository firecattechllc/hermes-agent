"""Real, disabled-by-default Supabase readiness: config, credential validation, health checks.

Hermes add-on continuation run, Sigil 3.8 track (per
``docs/roadmap/SIGIL_RELEASE_ROADMAP.md``'s existing Supabase entry, not
part of the Hermes add-on Phase A-J train). Scope: configuration and
read-only health/readiness checks against the official Supabase REST API
(PostgREST + GoTrue), using the officially documented, unauthenticated
``/auth/v1/health`` endpoint and the authenticated ``/rest/v1/`` root.

This module never fabricates credentials. ``resolve_supabase_credentials()``
reads real environment variables only and returns ``None`` when unset;
every health-check function treats a missing credential as a precise,
reportable blocker rather than raising or guessing a default.

No migration or schema-setup capability is implemented: this codebase does
not yet define any migration files for a Supabase project (checked: no
``supabase/migrations/`` directory exists in this repository), so there is
nothing to run. Building a migration runner ahead of any actual migration
being defined would be speculative scope, not implementation.
"""

from __future__ import annotations

import http.client
import os
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

SUPABASE_READINESS_SCHEMA_VERSION = 1

_PROJECT_URL_PATTERN = re.compile(r"^https://[a-z0-9]{20}\.supabase\.co/?$")
_MAX_RESPONSE_BYTES = 500_000
_USER_AGENT = "HermesSupabaseReadiness/1.0 (+https://github.com/firecattechllc/hermes-agent)"


class SupabaseReadinessError(RuntimeError):
    """A Supabase readiness operation failed closed."""


@dataclass(frozen=True, slots=True)
class SupabaseConfig:
    enabled: bool = False
    project_url: str = ""
    timeout_seconds: float = 10.0
    schema_version: int = SUPABASE_READINESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SUPABASE_READINESS_SCHEMA_VERSION:
            raise SupabaseReadinessError("unsupported Supabase readiness config schema")
        if not 0 < self.timeout_seconds <= 30:
            raise SupabaseReadinessError("timeout is outside bounds")
        if self.enabled and _PROJECT_URL_PATTERN.match(self.project_url) is None:
            raise SupabaseReadinessError(
                "project_url must look like https://<20-char-ref>.supabase.co"
            )


@dataclass(frozen=True, slots=True)
class SupabaseCredentials:
    """Real operator-supplied keys. Never a field on a logged/serialized object."""

    anon_key: str
    service_role_key: str | None = None

    def __post_init__(self) -> None:
        if not self.anon_key or not self.anon_key.strip():
            raise SupabaseReadinessError("anon_key cannot be blank")

    def __repr__(self) -> str:  # never leak keys via repr/logging
        return "SupabaseCredentials(anon_key=***redacted***, service_role_key=***redacted-or-none***)"


def resolve_supabase_credentials() -> SupabaseCredentials | None:
    """Resolve real credentials from the environment, or ``None``.

    Reads ``SUPABASE_ANON_KEY`` (required) and ``SUPABASE_SERVICE_ROLE_KEY``
    (optional, only used for the authenticated readiness probe -- never
    required for the public health check). Never returns a fabricated or
    default key.
    """

    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not anon_key:
        return None
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or None
    return SupabaseCredentials(anon_key=anon_key, service_role_key=service_role_key)


def _get(config: SupabaseConfig, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
    parsed = urlsplit(config.project_url)
    hostname = parsed.hostname
    if hostname is None:
        raise SupabaseReadinessError("project_url is missing a hostname")

    try:
        raw_sock = socket.create_connection((hostname, 443), timeout=config.timeout_seconds)
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(raw_sock, server_hostname=hostname)
        connection = http.client.HTTPSConnection(hostname, timeout=config.timeout_seconds)
        connection.sock = wrapped
        connection.request("GET", path, headers={**headers, "User-Agent": _USER_AGENT})
        response = connection.getresponse()
        body = response.read(_MAX_RESPONSE_BYTES)
        status = response.status
        connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
        raise SupabaseReadinessError(f"Supabase request failed: {error}") from error

    return status, body


def check_public_health(config: SupabaseConfig) -> dict[str, Any]:
    """``GET /auth/v1/health`` -- the one Supabase endpoint that needs no API key at all.

    Real network call when ``config.enabled``; proves the project exists
    and GoTrue (Supabase Auth) is reachable, independent of whether any
    credential has been configured yet.
    """

    if not config.enabled:
        raise SupabaseReadinessError("Supabase readiness checks are disabled by policy")

    status, body = _get(config, "/auth/v1/health", headers={})
    return {
        "status": status,
        "healthy": status == 200,
        "raw": body.decode("utf-8", errors="replace")[:2_000],
    }


def check_authenticated_readiness(
    config: SupabaseConfig, credentials: SupabaseCredentials
) -> dict[str, Any]:
    """``GET /rest/v1/`` with the anon key -- proves the API key is actually valid."""

    if not config.enabled:
        raise SupabaseReadinessError("Supabase readiness checks are disabled by policy")

    status, body = _get(
        config,
        "/rest/v1/",
        headers={"apikey": credentials.anon_key, "Authorization": f"Bearer {credentials.anon_key}"},
    )
    return {
        "status": status,
        "authenticated": status in (200, 404),  # PostgREST root with no tables can be 404; auth itself is what we're proving
        "raw": body.decode("utf-8", errors="replace")[:2_000],
    }


def readiness_status() -> dict[str, Any]:
    """Full, never-raising Mission Control projection: config + credential + live health."""

    enabled = os.environ.get("SIGIL_SUPABASE_ENABLED", "").strip().lower() in {"1", "true"}
    project_url = os.environ.get("SUPABASE_PROJECT_URL", "").strip()

    if not enabled:
        return {
            "configured": False,
            "healthy": False,
            "reason": "Supabase readiness is disabled by policy (set SIGIL_SUPABASE_ENABLED=true to enable).",
        }

    try:
        config = SupabaseConfig(enabled=True, project_url=project_url)
    except SupabaseReadinessError as error:
        return {"configured": False, "healthy": False, "reason": str(error)}

    credentials = resolve_supabase_credentials()

    try:
        health = check_public_health(config)
    except SupabaseReadinessError as error:
        return {"configured": credentials is not None, "healthy": False, "reason": str(error)}

    result: dict[str, Any] = {
        "configured": credentials is not None,
        "project_healthy": health["healthy"],
        "credential_present": credentials is not None,
    }

    if credentials is None:
        result["reason"] = "No SUPABASE_ANON_KEY is configured; project reachability confirmed, credential validity unknown."
        result["healthy"] = False
        return result

    try:
        auth = check_authenticated_readiness(config, credentials)
        result["authenticated"] = auth["authenticated"]
        result["healthy"] = health["healthy"] and auth["authenticated"]
    except SupabaseReadinessError as error:
        result["authenticated"] = False
        result["healthy"] = False
        result["reason"] = str(error)

    return result
