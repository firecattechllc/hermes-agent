from __future__ import annotations

import json
from urllib import error as urllib_error

from hermes_docs_worker.collectors import ollama_state
from hermes_docs_worker.status import StatusValue


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


def test_unreachable_endpoint_is_degraded(monkeypatch) -> None:
    def _raise(req, timeout):
        raise urllib_error.URLError("refused")

    monkeypatch.setattr("hermes_docs_worker.collectors.ollama_state.urllib_request.urlopen", _raise)
    facts = ollama_state.collect(endpoint="http://127.0.0.1:11434", timeout_seconds=1, now=0)
    assert len(facts) == 1
    assert facts[0].status == StatusValue.DEGRADED


def test_reachable_endpoint_reports_version_and_models(monkeypatch) -> None:
    responses = iter(
        [
            _FakeResponse(200, json.dumps({"version": "0.5.1"}).encode("utf-8")),
            _FakeResponse(200, json.dumps({"models": [{"name": "gemma3:4b"}]}).encode("utf-8")),
        ]
    )
    monkeypatch.setattr(
        "hermes_docs_worker.collectors.ollama_state.urllib_request.urlopen",
        lambda req, timeout: next(responses),
    )
    facts = ollama_state.collect(endpoint="http://127.0.0.1:11434", timeout_seconds=1, now=0)
    by_label = {f.label: f for f in facts}
    assert by_label["reachability"].status == StatusValue.VERIFIED
    assert "0.5.1" in by_label["reachability"].detail
    assert by_label["models"].status == StatusValue.VERIFIED
    assert "gemma3:4b" in by_label["models"].detail
