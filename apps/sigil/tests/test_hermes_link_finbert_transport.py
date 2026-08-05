from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from sigil.integrations.sentiment import (
    HermesLinkTitanFinBERTTransport,
    TitanFinBERTError,
    UrlLibHermesTaskClient,
)


@dataclass
class RecordingTaskClient:
    result: Mapping[str, Any] | None = None
    status: str = "completed"
    correlated: bool = True

    def __post_init__(self) -> None:
        self.envelopes: list[Mapping[str, Any]] = []

    def submit_task(self, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        self.envelopes.append(envelope)
        task_id = envelope["task_id"] if self.correlated else "wrong-task"
        return {
            "schema_version": 1,
            "task_id": task_id,
            "status": self.status,
            "result": self.result,
        }


def inference_request() -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "operation": "financial_sentiment",
        "model": "ProsusAI/finbert",
        "document": {"id": "fixture", "text": "Strong revenue growth."},
        "constraints": {
            "local_only": True,
            "allow_model_download": False,
            "allow_external_api": False,
            "allow_trade_execution": False,
        },
    }


def inference_result() -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "model": "ProsusAI/finbert",
        "scores": {"positive": 0.8, "neutral": 0.15, "negative": 0.05},
        "confidence": 0.8,
    }


def test_transport_wraps_inference_in_governed_task_envelope() -> None:
    client = RecordingTaskClient(result=inference_result())
    transport = HermesLinkTitanFinBERTTransport(client=client)

    result = transport.infer(request=inference_request())

    assert result == inference_result()
    envelope = client.envelopes[0]
    assert envelope["sender"] == "sigil"
    assert envelope["recipient"] == "titan-hermes"
    assert envelope["task_type"] == "sigil.finbert.inference.v1"
    assert envelope["risk"] == "low"
    assert envelope["requires_approval"] is False
    assert envelope["payload"]["operation"] == "financial_sentiment"
    assert envelope["capabilities"] == {
        "shell": False,
        "sudo": False,
        "network_external": False,
        "trade_execution": False,
        "spending": False,
        "publishing": False,
    }


def test_transport_rejects_uncorrelated_response() -> None:
    transport = HermesLinkTitanFinBERTTransport(
        client=RecordingTaskClient(result=inference_result(), correlated=False)
    )
    with pytest.raises(TitanFinBERTError, match="correlation"):
        transport.infer(request=inference_request())


@pytest.mark.parametrize("status", ["queued", "failed", "rejected"])
def test_transport_requires_completed_task(status: str) -> None:
    transport = HermesLinkTitanFinBERTTransport(
        client=RecordingTaskClient(result=inference_result(), status=status)
    )
    with pytest.raises(TitanFinBERTError, match="did not complete"):
        transport.infer(request=inference_request())


def test_transport_requires_structured_result() -> None:
    transport = HermesLinkTitanFinBERTTransport(
        client=RecordingTaskClient(result=None)
    )
    with pytest.raises(TitanFinBERTError, match="result object"):
        transport.infer(request=inference_request())


@pytest.mark.parametrize(
    "url",
    [
        "http://titan.local:8080",
        "ftp://titan.local",
        "https://",
    ],
)
def test_http_client_rejects_unsafe_or_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        UrlLibHermesTaskClient(base_url=url, bearer_token="fixture")


@pytest.mark.parametrize(
    "url",
    [
        "https://titan.example",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
)
def test_http_client_accepts_https_or_loopback(url: str) -> None:
    client = UrlLibHermesTaskClient(base_url=url, bearer_token="fixture")
    assert client.base_url == url


def test_http_client_requires_authentication() -> None:
    with pytest.raises(ValueError, match="token"):
        UrlLibHermesTaskClient(
            base_url="https://titan.example",
            bearer_token=" ",
        )
