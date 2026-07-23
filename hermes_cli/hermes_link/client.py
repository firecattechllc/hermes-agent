"""Typed Mac-side client with an injectable HTTP transport."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from .models import ClientResult, HermesLinkEnvelope, HermesLinkStatus, LinkError


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
        token: str,
        transport: Transport | None = None,
        connect_timeout: float = 2.0,
        read_timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
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
            response = self._transport.request(
                method,
                self.base_url + path,
                headers={"Authorization": f"Bearer {self._token}"},
                json=None if envelope is None else envelope.model_dump(mode="json"),
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
