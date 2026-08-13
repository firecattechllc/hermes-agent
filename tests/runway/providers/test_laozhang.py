from __future__ import annotations

import json
import logging
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from runway.flags import RunwayFeatureFlags
from runway.omniroute_port import ExecutionRequest
from runway.pricing import UsageEstimate
from runway.providers.credentials import CredentialResolutionError
from runway.providers.errors import ExecutionFailureCategory
from runway.providers.laozhang import (
    LaoZhangConfig,
    LaoZhangExecutionAdapter,
    _MAX_CANARY_OUTPUT_TOKENS,
    list_advertised_models,
    run_bounded_live_canary,
)

_SECRET = "sk-live-should-never-appear-anywhere-in-output"


class _FakeCredentialResolver:
    def resolve(self, credential_ref: str) -> str:
        assert credential_ref == "env:LAOZHANG_API_KEY"
        return _SECRET


class _FailingCredentialResolver:
    def resolve(self, credential_ref: str) -> str:
        raise CredentialResolutionError("no such env var")


def _authorized_flags() -> RunwayFeatureFlags:
    """The ONLY way to get external_execution_enabled=True: pydantic's
    model_construct bypasses validation. This exists solely so this test
    file can unit-test LaoZhangExecutionAdapter's HTTP logic against a
    mocked transport. Every real construction path (RunwayFeatureFlags(),
    RunwayFeatureFlags.from_env()) still hard-refuses True — see
    tests/runway/test_flags.py::test_external_execution_enabled_cannot_be_set_true.
    """
    return RunwayFeatureFlags.model_construct(
        runway_enabled=True, discovery_enabled=True,
        synthetic_qualification_enabled=True, external_execution_enabled=True,
    )


def _config(**overrides) -> LaoZhangConfig:
    fields = dict(base_url="https://api2.laozhang.ai/v1", credential_ref="env:LAOZHANG_API_KEY")
    fields.update(overrides)
    return LaoZhangConfig(**fields)


def _request(**overrides) -> ExecutionRequest:
    fields = dict(
        decision_id="dec-1", provider_id="laozhang", model_key="gpt-4o-mini@laozhang",
        usage_estimate=UsageEstimate(input_tokens=10, output_tokens=8),
        messages=({"role": "user", "content": "hello"},),
    )
    fields.update(overrides)
    return ExecutionRequest(**fields)


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def _success_body(*, model="gpt-4o-mini", prompt_tokens=12, completion_tokens=3, request_id="req-abc") -> bytes:
    return json.dumps({
        "id": request_id, "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                   "total_tokens": prompt_tokens + completion_tokens},
    }).encode("utf-8")


# ── endpoint validation ──────────────────────────────────────────────────

def test_endpoint_validation_rejects_http():
    with pytest.raises(ValidationError):
        _config(base_url="http://api2.laozhang.ai/v1")


def test_endpoint_validation_rejects_query_or_fragment():
    with pytest.raises(ValidationError):
        _config(base_url="https://api2.laozhang.ai/v1?key=abc")
    with pytest.raises(ValidationError):
        _config(base_url="https://api2.laozhang.ai/v1#frag")


def test_endpoint_validation_rejects_empty_or_hostless():
    with pytest.raises(ValidationError):
        _config(base_url="")
    with pytest.raises(ValidationError):
        _config(base_url="https://")


def test_endpoint_validation_rejects_timeout_out_of_bounds():
    with pytest.raises(ValidationError):
        _config(timeout_seconds=0)
    with pytest.raises(ValidationError):
        _config(timeout_seconds=1000)


# ── credential isolation ─────────────────────────────────────────────────

def test_credential_ref_rejects_raw_secret_shaped_value():
    with pytest.raises(ValidationError):
        _config(credential_ref="sk-abcdef1234567890")
    with pytest.raises(ValidationError):
        _config(credential_ref="api_key=abcdef")
    with pytest.raises(ValidationError):
        _config(credential_ref="")


def test_execute_never_stores_secret_on_adapter_or_logs_it(caplog):
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with caplog.at_level(logging.DEBUG):
        with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(_success_body())):
            result = adapter.execute(_request())
    assert result.succeeded is True
    assert _SECRET not in caplog.text
    assert _SECRET not in result.model_dump_json()
    assert not hasattr(adapter, "_api_key")
    assert _SECRET not in json.dumps(vars(adapter), default=str)


# ── authorization gates (denial) ────────────────────────────────────────

def test_global_external_execution_denial_blocks_before_any_network_call():
    adapter = LaoZhangExecutionAdapter(_config(), flags=RunwayFeatureFlags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request())
    mock_urlopen.assert_not_called()
    assert result.succeeded is False
    assert result.error == ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED.value


def test_wrong_provider_id_rejected_without_network_call():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request(provider_id="some-other-provider"))
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.INVALID_REQUEST.value


def test_empty_messages_rejected_without_network_call():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request(messages=()))
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.INVALID_REQUEST.value


def test_credential_resolution_failure_denies_without_network_call():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FailingCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request())
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.AUTH_FAILED.value


def test_model_key_not_belonging_to_provider_rejected():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request(model_key="gpt-4o-mini@some-other-provider"))
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.INVALID_REQUEST.value


# ── Authorization header + request serialization ───────────────────────

def test_authorization_header_and_request_serialization():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(_success_body())) as mock_urlopen:
        adapter.execute(_request())

    mock_urlopen.assert_called_once()
    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == "https://api2.laozhang.ai/v1/chat/completions"
    assert sent_request.get_header("Authorization") == f"Bearer {_SECRET}"
    assert sent_request.get_header("Content-type") == "application/json"
    assert mock_urlopen.call_args.kwargs["timeout"] == _config().timeout_seconds

    body = json.loads(sent_request.data)
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["stream"] is False
    assert body["max_tokens"] == 8


def test_model_selection_is_not_hardcoded():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    for model_key, expected_upstream in [("gpt-4o-mini@laozhang", "gpt-4o-mini"), ("claude-3-5-sonnet@laozhang", "claude-3-5-sonnet")]:
        with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(_success_body(model=expected_upstream))) as mock_urlopen:
            adapter.execute(_request(model_key=model_key))
        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["model"] == expected_upstream


def test_model_overrides_take_precedence_over_derivation():
    config = _config(model_overrides={"alias@laozhang": "gpt-4o-mini-2024-07-18"})
    adapter = LaoZhangExecutionAdapter(config, flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(_success_body())) as mock_urlopen:
        adapter.execute(_request(model_key="alias@laozhang"))
    body = json.loads(mock_urlopen.call_args[0][0].data)
    assert body["model"] == "gpt-4o-mini-2024-07-18"


# ── response normalization / token usage parsing ────────────────────────

def test_token_usage_and_identity_parsing():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    body = _success_body(model="gpt-4o-mini", prompt_tokens=21, completion_tokens=4, request_id="req-xyz")
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
        result = adapter.execute(_request())
    assert result.succeeded is True
    assert result.actual_input_tokens == 21
    assert result.actual_output_tokens == 4
    assert result.reported_model == "gpt-4o-mini"
    assert result.provider_request_id == "req-xyz"
    assert result.latency_ms >= 0


def test_malformed_response_missing_choices():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    body = json.dumps({"id": "x", "model": "gpt-4o-mini", "usage": {}}).encode()
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
        result = adapter.execute(_request())
    assert result.succeeded is False
    assert result.error == ExecutionFailureCategory.MALFORMED_RESPONSE.value


def test_malformed_response_invalid_json():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(b"not json{{{")):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.MALFORMED_RESPONSE.value


def test_malformed_response_non_numeric_usage():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    body = json.dumps({
        "id": "x", "model": "gpt-4o-mini",
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": "lots", "completion_tokens": 3},
    }).encode()
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.MALFORMED_RESPONSE.value


# ── HTTP error normalization / rate limits / timeouts ───────────────────

@pytest.mark.parametrize("status,expected", [
    (401, ExecutionFailureCategory.AUTH_FAILED),
    (403, ExecutionFailureCategory.AUTH_FAILED),
    (429, ExecutionFailureCategory.RATE_LIMITED),
    (400, ExecutionFailureCategory.INVALID_REQUEST),
    (500, ExecutionFailureCategory.PROVIDER_ERROR),
    (503, ExecutionFailureCategory.PROVIDER_ERROR),
])
def test_http_error_normalization(status, expected):
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    error = urllib.error.HTTPError(url="https://api2.laozhang.ai/v1/chat/completions", code=status, msg="err", hdrs=None, fp=None)
    with patch("runway.providers.laozhang.urllib.request.urlopen", side_effect=error):
        result = adapter.execute(_request())
    assert result.succeeded is False
    assert result.error == expected.value


def test_timeout_error_normalized():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.TIMEOUT.value


def test_urlerror_wrapping_timeout_normalized_as_timeout():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    wrapped = urllib.error.URLError(TimeoutError("timed out"))
    with patch("runway.providers.laozhang.urllib.request.urlopen", side_effect=wrapped):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.TIMEOUT.value


def test_generic_network_error_normalized():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    wrapped = urllib.error.URLError(OSError("connection refused"))
    with patch("runway.providers.laozhang.urllib.request.urlopen", side_effect=wrapped):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.NETWORK_FAILURE.value


def test_timeout_is_always_bounded_and_passed_through():
    config = _config(timeout_seconds=5.0)
    adapter = LaoZhangExecutionAdapter(config, flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(_success_body())) as mock_urlopen:
        adapter.execute(_request())
    assert mock_urlopen.call_args.kwargs["timeout"] == 5.0


# ── no automatic retry ───────────────────────────────────────────────────

def test_no_automatic_retry_on_failure():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    error = urllib.error.HTTPError(url="x", code=500, msg="err", hdrs=None, fp=None)
    with patch("runway.providers.laozhang.urllib.request.urlopen", side_effect=error) as mock_urlopen:
        adapter.execute(_request())
    mock_urlopen.assert_called_once()


def test_single_call_on_success_too():
    adapter = LaoZhangExecutionAdapter(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(_success_body())) as mock_urlopen:
        adapter.execute(_request())
    mock_urlopen.assert_called_once()


# ── model discovery (Phase C) ────────────────────────────────────────────

def test_list_advertised_models_requires_flag():
    with pytest.raises(PermissionError):
        with patch("runway.providers.laozhang.urllib.request.urlopen") as mock_urlopen:
            list_advertised_models(_config(), flags=RunwayFeatureFlags(), credential_resolver=_FakeCredentialResolver())
    mock_urlopen.assert_not_called()


def test_list_advertised_models_parses_catalog():
    body = json.dumps({"object": "list", "data": [
        {"id": "gpt-4o-mini", "object": "model", "owned_by": "laozhang", "created": 1700000000},
        {"id": "claude-3-5-sonnet", "object": "model", "owned_by": "laozhang"},
        {"not_a_valid_entry": True},
    ]}).encode()
    with patch("runway.providers.laozhang.urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
        models = list_advertised_models(_config(), flags=_authorized_flags(), credential_resolver=_FakeCredentialResolver())
    assert len(models) == 2
    assert {m.id for m in models} == {"gpt-4o-mini", "claude-3-5-sonnet"}


# ── bounded live canary (Phase E) ────────────────────────────────────────

def test_run_bounded_live_canary_requires_explicit_opt_in():
    fake_adapter = MagicMock(spec=LaoZhangExecutionAdapter)
    with pytest.raises(PermissionError):
        run_bounded_live_canary(fake_adapter, model_key="gpt-4o-mini@laozhang", dangerously_i_understand_this_spends_real_money=False)
    fake_adapter.execute.assert_not_called()


def test_run_bounded_live_canary_sends_exactly_one_bounded_request():
    fake_adapter = MagicMock(spec=LaoZhangExecutionAdapter)
    fake_adapter.execute.return_value = MagicMock(
        succeeded=True, reported_model="gpt-4o-mini", provider_request_id="req-1",
        latency_ms=120, actual_input_tokens=8, actual_output_tokens=1, error="",
    )
    result = run_bounded_live_canary(
        fake_adapter, model_key="gpt-4o-mini@laozhang", dangerously_i_understand_this_spends_real_money=True,
    )
    fake_adapter.execute.assert_called_once()
    sent = fake_adapter.execute.call_args[0][0]
    assert len(sent.messages) == 1
    assert sent.usage_estimate.output_tokens <= _MAX_CANARY_OUTPUT_TOKENS
    assert result.succeeded is True
    assert result.identity_note  # provider-claimed-only disclaimer always present
