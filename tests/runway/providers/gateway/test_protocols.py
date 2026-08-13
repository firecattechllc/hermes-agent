from __future__ import annotations

import json

from runway.providers.gateway.protocol import categorize_http_status, handler_for
from runway.providers.gateway.models import GatewayProtocol
from runway.providers.errors import ExecutionFailureCategory
import runway.providers.gateway.protocols  # noqa: F401 -- registers handlers

_MESSAGES = ({"role": "user", "content": "hello"},)


def test_openai_chat_completions_request_serialization():
    handler = handler_for(GatewayProtocol.OPENAI_CHAT_COMPLETIONS)
    prepared = handler.build_request(
        base_url="https://api.example.test/v1", api_key="secret-key",
        upstream_model="gpt-4o-mini", messages=_MESSAGES, max_output_tokens=16,
    )
    assert prepared.url == "https://api.example.test/v1/chat/completions"
    assert prepared.headers["authorization"] == "Bearer secret-key"
    body = json.loads(prepared.body)
    assert body == {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}],
                     "stream": False, "max_tokens": 16}


def test_openai_chat_completions_response_normalization():
    handler = handler_for(GatewayProtocol.OPENAI_CHAT_COMPLETIONS)
    raw = json.dumps({
        "id": "req-1", "model": "gpt-4o-mini",
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }).encode()
    parsed = handler.parse_success(raw)
    assert parsed.succeeded is True
    assert (parsed.input_tokens, parsed.output_tokens) == (10, 3)
    assert parsed.reported_model == "gpt-4o-mini"
    assert parsed.provider_request_id == "req-1"


def test_openai_responses_request_serialization():
    handler = handler_for(GatewayProtocol.OPENAI_RESPONSES)
    prepared = handler.build_request(
        base_url="https://api.example.test/v1", api_key="secret-key",
        upstream_model="gpt-4o-mini", messages=_MESSAGES, max_output_tokens=16,
    )
    assert prepared.url == "https://api.example.test/v1/responses"
    assert prepared.headers["authorization"] == "Bearer secret-key"
    body = json.loads(prepared.body)
    assert body == {"model": "gpt-4o-mini", "input": [{"role": "user", "content": "hello"}], "max_output_tokens": 16}


def test_openai_responses_response_normalization():
    handler = handler_for(GatewayProtocol.OPENAI_RESPONSES)
    raw = json.dumps({
        "id": "resp-1", "model": "gpt-4o-mini",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }).encode()
    parsed = handler.parse_success(raw)
    assert parsed.succeeded is True
    assert (parsed.input_tokens, parsed.output_tokens) == (12, 4)
    assert parsed.reported_model == "gpt-4o-mini"
    assert parsed.provider_request_id == "resp-1"


def test_anthropic_messages_request_serialization_uses_x_api_key_not_bearer():
    handler = handler_for(GatewayProtocol.ANTHROPIC_MESSAGES)
    prepared = handler.build_request(
        base_url="https://api.example.test/v1", api_key="secret-key",
        upstream_model="claude-opus-5", messages=_MESSAGES, max_output_tokens=16,
    )
    assert prepared.url == "https://api.example.test/v1/messages"
    assert prepared.headers["x-api-key"] == "secret-key"
    assert "authorization" not in prepared.headers
    assert prepared.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(prepared.body)
    assert body == {"model": "claude-opus-5", "max_tokens": 16, "messages": [{"role": "user", "content": "hello"}]}


def test_anthropic_messages_max_tokens_required_defaults_when_zero():
    handler = handler_for(GatewayProtocol.ANTHROPIC_MESSAGES)
    prepared = handler.build_request(
        base_url="https://api.example.test/v1", api_key="k",
        upstream_model="claude-opus-5", messages=_MESSAGES, max_output_tokens=0,
    )
    body = json.loads(prepared.body)
    assert body["max_tokens"] > 0  # Anthropic requires max_tokens; never send 0/absent


def test_anthropic_messages_response_normalization():
    handler = handler_for(GatewayProtocol.ANTHROPIC_MESSAGES)
    raw = json.dumps({
        "id": "msg-1", "type": "message", "model": "claude-opus-5",
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }).encode()
    parsed = handler.parse_success(raw)
    assert parsed.succeeded is True
    assert (parsed.input_tokens, parsed.output_tokens) == (20, 5)
    assert parsed.reported_model == "claude-opus-5"
    assert parsed.provider_request_id == "msg-1"


def test_all_protocols_reject_malformed_response_missing_payload():
    for protocol in GatewayProtocol:
        handler = handler_for(protocol)
        parsed = handler.parse_success(b"not json{{{")
        assert parsed.succeeded is False
        assert parsed.error == "malformed_response"


def test_all_protocols_reject_empty_content_but_still_capture_reported_model():
    empty_bodies = {
        GatewayProtocol.OPENAI_CHAT_COMPLETIONS: {"model": "m", "choices": []},
        GatewayProtocol.OPENAI_RESPONSES: {"model": "m", "output": []},
        GatewayProtocol.ANTHROPIC_MESSAGES: {"model": "m", "content": []},
    }
    for protocol, body in empty_bodies.items():
        handler = handler_for(protocol)
        parsed = handler.parse_success(json.dumps(body).encode())
        assert parsed.succeeded is False
        assert parsed.reported_model == "m"


def test_categorize_http_status_shared_across_protocols():
    assert categorize_http_status(401) == ExecutionFailureCategory.AUTH_FAILED
    assert categorize_http_status(403) == ExecutionFailureCategory.AUTH_FAILED
    assert categorize_http_status(429) == ExecutionFailureCategory.RATE_LIMITED
    assert categorize_http_status(400) == ExecutionFailureCategory.INVALID_REQUEST
    assert categorize_http_status(404) == ExecutionFailureCategory.INVALID_REQUEST
    assert categorize_http_status(413) == ExecutionFailureCategory.INVALID_REQUEST
    assert categorize_http_status(500) == ExecutionFailureCategory.PROVIDER_ERROR
    assert categorize_http_status(529) == ExecutionFailureCategory.PROVIDER_ERROR  # Anthropic "overloaded"
