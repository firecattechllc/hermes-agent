"""Typed Mac-side client with an injectable HTTP transport."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from .models import ClientResult, HermesLinkEnvelope, HermesLinkStatus, LinkError
from .security import CredentialRegistry, build_signed_request, resolve_secret


class Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class Transport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class HermesLinkClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        credential_registry: CredentialRegistry | None = None,
        coordinator_node_id: str | None = None,
        target_node_id: str | None = None,
        transport: Transport | None = None,
        connect_timeout: float = 2.0,
        read_timeout: float = 10.0,
    ) -> None:
        if (token is None) == (credential_registry is None):
            raise ValueError(
                "exactly one Hermes Link client authentication mode is required"
            )
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._credential_registry = credential_registry
        self._coordinator_node_id = coordinator_node_id
        self._target_node_id = target_node_id
        self._transport = transport or httpx.Client(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout)
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        envelope: HermesLinkEnvelope | None = None,
        kind: str = "envelope",
    ) -> ClientResult:
        try:
            body = (
                b""
                if envelope is None
                else json.dumps(
                    envelope.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
            headers: dict[str, str]
            if self._credential_registry is not None:
                if self._coordinator_node_id is None or self._target_node_id is None:
                    raise ValueError("signed client node identities are required")
                credential = self._credential_registry.active_for(
                    self._coordinator_node_id,
                    self._target_node_id,
                    now=int(time.time()),
                )
                signed = build_signed_request(method, path, body, credential)
                headers = signed.headers(resolve_secret(credential.secret_reference))
                if body:
                    headers["Content-Type"] = "application/json"
            else:
                headers = {"Authorization": f"Bearer {self._token}"}
            response = self._transport.request(
                method,
                self.base_url + path,
                headers=headers,
                content=body,
            )
            data = response.json()
            if response.status_code >= 400:
                detail = (
                    data.get("error", data.get("detail", {}))
                    if isinstance(data, dict)
                    else {}
                )
                if isinstance(detail, dict) and "error" in detail:
                    detail = detail["error"]
                return ClientResult(
                    ok=False,
                    error=LinkError(
                        code=detail.get("code", f"http_{response.status_code}"),
                        message=detail.get("message", "Titan rejected the request"),
                        retryable=response.status_code >= 500,
                    ),
                )
            if kind == "status":
                return ClientResult(
                    ok=True, status=HermesLinkStatus.model_validate(data)
                )
            if kind == "queue":
                return ClientResult(
                    ok=True,
                    queue=tuple(
                        HermesLinkEnvelope.model_validate(item)
                        for item in data.get("messages", [])
                    ),
                )
            return ClientResult(
                ok=True, envelope=HermesLinkEnvelope.model_validate(data)
            )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            return ClientResult(
                ok=False,
                error=LinkError(
                    code="titan_unreachable",
                    message="Titan Hermes is offline or unreachable",
                    retryable=True,
                ),
            )
        except Exception:
            return ClientResult(
                ok=False,
                error=LinkError(
                    code="invalid_response",
                    message="Titan returned an invalid structured response",
                    retryable=False,
                ),
            )

    def fetch_status(self) -> ClientResult:
        return self._request("GET", "/status", kind="status")

    def list_queue(self) -> ClientResult:
        return self._request("GET", "/queue", kind="queue")

    def send_chat(self, envelope: HermesLinkEnvelope) -> ClientResult:
        return self._request("POST", "/chat", envelope=envelope)

    def submit_task(self, envelope: HermesLinkEnvelope) -> ClientResult:
        return self._request("POST", "/task", envelope=envelope)

    def deliver_lesson(self, envelope: HermesLinkEnvelope) -> ClientResult:
        return self._request("POST", "/lesson", envelope=envelope)

    def latest_report(self) -> ClientResult:
        return self._request("GET", "/reports/latest")
