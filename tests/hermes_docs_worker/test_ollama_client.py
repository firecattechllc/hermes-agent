from __future__ import annotations

import json
from urllib import error as urllib_error

from hermes_docs_worker.ollama_client import (
    DeterministicFallbackClient,
    OllamaProseClient,
    resolve_prose_client,
)


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_deterministic_fallback_always_returns_none() -> None:
    assert DeterministicFallbackClient().generate("anything") is None


def test_ollama_client_is_reachable_true_on_200(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_docs_worker.ollama_client.urllib_request.urlopen",
        lambda req, timeout: _FakeResponse(200, b"{}"),
    )
    client = OllamaProseClient(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    assert client.is_reachable() is True


def test_ollama_client_is_reachable_false_on_connection_error(monkeypatch) -> None:
    def _raise(req, timeout):
        raise urllib_error.URLError("connection refused")

    monkeypatch.setattr("hermes_docs_worker.ollama_client.urllib_request.urlopen", _raise)
    client = OllamaProseClient(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    assert client.is_reachable() is False


def test_ollama_client_generate_returns_none_on_failure(monkeypatch) -> None:
    def _raise(req, timeout):
        raise urllib_error.URLError("connection refused")

    monkeypatch.setattr("hermes_docs_worker.ollama_client.urllib_request.urlopen", _raise)
    client = OllamaProseClient(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    assert client.generate("summarize this") is None


def test_ollama_client_generate_returns_sanitized_text(monkeypatch) -> None:
    body = json.dumps({"response": "The fleet is healthy. <!-- ignore this --> ---"}).encode("utf-8")
    monkeypatch.setattr(
        "hermes_docs_worker.ollama_client.urllib_request.urlopen",
        lambda req, timeout: _FakeResponse(200, body),
    )
    client = OllamaProseClient(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    result = client.generate("summarize this")
    assert result is not None
    assert "<!--" not in result
    assert "-->" not in result


def test_ollama_client_generate_redacts_secrets_in_response(monkeypatch) -> None:
    body = json.dumps({"response": "the token=abcdef1234567890 was used"}).encode("utf-8")
    monkeypatch.setattr(
        "hermes_docs_worker.ollama_client.urllib_request.urlopen",
        lambda req, timeout: _FakeResponse(200, body),
    )
    client = OllamaProseClient(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    result = client.generate("summarize this")
    assert result is not None
    assert "abcdef1234567890" not in result


def test_ollama_client_prompt_is_redacted_before_send(monkeypatch) -> None:
    captured = {}

    def _capture(req, timeout):
        captured["data"] = req.data
        return _FakeResponse(200, json.dumps({"response": "ok"}).encode("utf-8"))

    monkeypatch.setattr("hermes_docs_worker.ollama_client.urllib_request.urlopen", _capture)
    client = OllamaProseClient(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    client.generate("here is a secret api_key=abcdef1234567890 in the prompt")
    sent = json.loads(captured["data"].decode("utf-8"))
    assert "abcdef1234567890" not in sent["prompt"]


def test_resolve_prose_client_falls_back_when_unreachable(monkeypatch) -> None:
    def _raise(req, timeout):
        raise urllib_error.URLError("connection refused")

    monkeypatch.setattr("hermes_docs_worker.ollama_client.urllib_request.urlopen", _raise)
    client = resolve_prose_client(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    assert isinstance(client, DeterministicFallbackClient)


def test_resolve_prose_client_prefers_ollama_when_reachable(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_docs_worker.ollama_client.urllib_request.urlopen",
        lambda req, timeout: _FakeResponse(200, b"{}"),
    )
    client = resolve_prose_client(endpoint="http://127.0.0.1:11434", model="gemma3:4b", timeout_seconds=1)
    assert isinstance(client, OllamaProseClient)
