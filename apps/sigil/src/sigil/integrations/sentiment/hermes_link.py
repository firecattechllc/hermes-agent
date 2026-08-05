from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .titan import TitanFinBERTError, TitanFinBERTTransport


class HermesTaskClient(Protocol):
    """Minimal governed client boundary for Hermes POST /task."""

    def submit_task(self, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        """Submit one task and return its structured result envelope."""


@dataclass(frozen=True, slots=True)
class UrlLibHermesTaskClient:
    """Small authenticated HTTP client for the governed Hermes task endpoint."""

    base_url: str
    bearer_token: str
    timeout_seconds: float = 15.0
    max_response_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if not parsed.hostname:
            raise ValueError("Hermes link URL must include a hostname")
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("Hermes link must use HTTPS or loopback HTTP")
        if not self.bearer_token.strip():
            raise ValueError("Hermes bearer token is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

    def submit_task(self, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        request = Request(
            url=f"{self.base_url.rstrip('/')}/task",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "sigil-hermes-link/1",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as exc:
            raise TitanFinBERTError(
                f"Hermes task endpoint returned HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise TitanFinBERTError("Hermes task endpoint is unavailable") from exc
        except TimeoutError as exc:
            raise TitanFinBERTError("Hermes task request timed out") from exc

        if len(raw) > self.max_response_bytes:
            raise TitanFinBERTError("Hermes task response exceeded size limit")

        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TitanFinBERTError("Hermes task response was not valid JSON") from exc

        if not isinstance(decoded, dict):
            raise TitanFinBERTError("Hermes task response must be a JSON object")
        return decoded


@dataclass(frozen=True, slots=True)
class HermesLinkTitanFinBERTTransport(TitanFinBERTTransport):
    """Submit FinBERT inference as a governed Hermes task."""

    client: HermesTaskClient
    sender: str = "sigil"
    recipient: str = "titan-hermes"
    task_type: str = "sigil.finbert.inference.v1"

    def infer(self, *, request: Mapping[str, Any]) -> Mapping[str, Any]:
        task_id = f"sigil-finbert-{uuid4()}"
        envelope = {
            "schema_version": 1,
            "message_type": "task",
            "task_id": task_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "task_type": self.task_type,
            "risk": "low",
            "requires_approval": False,
            "payload": dict(request),
            "capabilities": {
                "shell": False,
                "sudo": False,
                "network_external": False,
                "trade_execution": False,
                "spending": False,
                "publishing": False,
            },
        }

        response = self.client.submit_task(envelope=envelope)
        return self._extract_result(response=response, task_id=task_id)

    @staticmethod
    def _extract_result(
        *,
        response: Mapping[str, Any],
        task_id: str,
    ) -> Mapping[str, Any]:
        if response.get("schema_version") != 1:
            raise TitanFinBERTError("unsupported Hermes task response schema")
        if response.get("task_id") != task_id:
            raise TitanFinBERTError("Hermes task response correlation failed")
        if response.get("status") != "completed":
            detail = response.get("error") or response.get("status") or "unknown"
            raise TitanFinBERTError(f"Hermes FinBERT task did not complete: {detail}")

        result = response.get("result")
        if not isinstance(result, Mapping):
            raise TitanFinBERTError("Hermes task response is missing a result object")
        return result
