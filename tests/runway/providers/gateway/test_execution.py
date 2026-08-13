from __future__ import annotations

import json
import logging
import urllib.error
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from runway.flags import RunwayFeatureFlags
from runway.omniroute_port import ExecutionRequest
from runway.pricing import UsageEstimate
from runway.providers.credentials import CredentialResolutionError
from runway.providers.errors import ExecutionFailureCategory
from runway.providers.gateway.execution import GatewayExecutionAdapter
from runway.providers.gateway.models import GatewayProtocol
from runway.providers.gateway.registry import ChannelRegistry

from .conftest import make_channel, make_provider

_SECRET = "sk-live-should-never-appear-anywhere"


class _FakeResolver:
    def resolve(self, ref: str) -> str:
        return _SECRET


class _FailingResolver:
    def resolve(self, ref: str) -> str:
        raise CredentialResolutionError("missing")


def _authorized_flags() -> RunwayFeatureFlags:
    return RunwayFeatureFlags.model_construct(
        runway_enabled=True, discovery_enabled=True, synthetic_qualification_enabled=True,
        external_execution_enabled=True,
    )


def _registry_with_channel(**channel_overrides):
    provider = make_provider("acme")
    channel = make_channel(provider_id="acme", **channel_overrides)
    registry = ChannelRegistry(
        providers=(provider,), channels=(channel,), model_channel_map={"m@acme": channel.channel_id},
    ).authorize_channel(channel.channel_id)
    return registry, channel


def _request(**overrides) -> ExecutionRequest:
    fields = dict(
        decision_id="d1", provider_id="acme", model_key="m@acme",
        usage_estimate=UsageEstimate(input_tokens=5, output_tokens=8),
        messages=({"role": "user", "content": "hi"},),
    )
    fields.update(overrides)
    return ExecutionRequest(**fields)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def _success_body(model="m") -> bytes:
    return json.dumps({
        "id": "req-1", "model": model, "choices": [{"message": {"content": "pong"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }).encode()


def test_global_gate_blocks_before_any_network_call():
    registry, _ = _registry_with_channel()
    adapter = GatewayExecutionAdapter(registry, flags=RunwayFeatureFlags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request())
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED.value


def test_unauthorized_channel_blocks_without_network_call():
    provider = make_provider("acme")
    channel = make_channel(provider_id="acme")
    registry = ChannelRegistry(
        providers=(provider,), channels=(channel,), model_channel_map={"m@acme": channel.channel_id},
    )  # never authorized
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request())
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED.value


def test_no_channel_for_model_rejected():
    registry, _ = _registry_with_channel()
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request(model_key="other@acme"))
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.INVALID_REQUEST.value


def test_empty_messages_rejected():
    registry, _ = _registry_with_channel()
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request(messages=()))
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.INVALID_REQUEST.value


def test_credential_resolution_failure_blocks_without_network_call():
    registry, _ = _registry_with_channel()
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FailingResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen") as mock_urlopen:
        result = adapter.execute(_request())
    mock_urlopen.assert_not_called()
    assert result.error == ExecutionFailureCategory.AUTH_FAILED.value


def test_dispatches_to_the_configured_protocol_and_never_logs_secret(caplog):
    registry, channel = _registry_with_channel(protocol=GatewayProtocol.ANTHROPIC_MESSAGES)
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    body = json.dumps({
        "id": "msg-1", "model": "m", "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }).encode()
    with caplog.at_level(logging.DEBUG):
        with patch("runway.providers.gateway.execution.urllib.request.urlopen", return_value=_FakeResponse(body)) as mock_urlopen:
            result = adapter.execute(_request())
        sent = mock_urlopen.call_args[0][0]
    assert sent.get_header("X-api-key") == _SECRET
    assert result.succeeded is True
    assert result.actual_input_tokens == 5
    assert _SECRET not in caplog.text
    assert _SECRET not in result.model_dump_json()


def test_no_automatic_retry_single_call_only():
    registry, _ = _registry_with_channel()
    error = urllib.error.HTTPError(url="x", code=500, msg="err", hdrs=None, fp=None)
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen", side_effect=error) as mock_urlopen:
        adapter.execute(_request())
    mock_urlopen.assert_called_once()


def test_http_error_normalized_via_shared_categorizer():
    registry, _ = _registry_with_channel()
    error = urllib.error.HTTPError(url="x", code=429, msg="err", hdrs=None, fp=None)
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen", side_effect=error):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.RATE_LIMITED.value


def test_timeout_normalized():
    registry, _ = _registry_with_channel()
    adapter = GatewayExecutionAdapter(registry, flags=_authorized_flags(), credential_resolver=_FakeResolver())
    with patch("runway.providers.gateway.execution.urllib.request.urlopen", side_effect=TimeoutError()):
        result = adapter.execute(_request())
    assert result.error == ExecutionFailureCategory.TIMEOUT.value


def test_endpoint_validation_rejects_embedded_credentials_in_url():
    with pytest.raises(ValidationError):
        make_channel(provider_id="acme", base_url="https://user:pass@api.example.test/v1")


def test_endpoint_validation_rejects_query_string_credentials():
    with pytest.raises(ValidationError):
        make_channel(provider_id="acme", base_url="https://api.example.test/v1?api_key=abc")


def test_endpoint_validation_rejects_http():
    with pytest.raises(ValidationError):
        make_channel(provider_id="acme", base_url="http://api.example.test/v1")
