"""Minimal, stdlib-only HTTP client for a worker node to reach Prime.

Fleet Unification live-runtime work. Companion to
:mod:`hermes_cli.prime.server` — used by Titan and Mac worker service
entrypoints (:mod:`hermes_cli.prime.entrypoints`) to actually register and
heartbeat against a remote Prime control plane over the Tailscale network,
using only ``urllib`` (no new third-party dependency).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest
from hermes_cli.prime.heartbeat import HeartbeatSubmission


class PrimeClientError(RuntimeError):
    """A request to the Prime control plane failed."""


class PrimeHTTPClient:
    def __init__(
        self, *, base_url: str, auth_token: str, timeout_seconds: float = 10.0
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Prime base_url must be an http(s) URL")
        if not auth_token:
            raise ValueError("Prime auth_token must be non-empty")
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._auth_token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail: object
            try:
                detail = json.loads(error.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                detail = str(error)
            raise PrimeClientError(f"Prime request to {path} failed ({error.code}): {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise PrimeClientError(f"Prime unreachable at {self._base_url}{path}: {error}") from error

    def register_node(self, request: FleetNodeRegistrationRequest) -> dict:
        return self._post("/v1/fleet/nodes/register", request.model_dump(mode="json"))

    def heartbeat(self, submission: HeartbeatSubmission) -> dict:
        return self._post("/v1/fleet/nodes/heartbeat", submission.model_dump(mode="json"))

    def health(self) -> Optional[dict]:
        request = urllib.request.Request(f"{self._base_url}/v1/fleet/health", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
