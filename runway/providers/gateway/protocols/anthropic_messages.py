"""Anthropic Messages API (``POST {base_url}/messages``).

Confirmed shape: auth is ``x-api-key: <key>`` (NOT ``Authorization: Bearer``)
plus a required ``anthropic-version`` header; request body
``{model, max_tokens, messages}`` (``max_tokens`` is required, unlike the
OpenAI-family adapters where it's optional); response
``{id, type: "message", content: [...], model, usage: {input_tokens,
output_tokens}}``; errors as HTTP status +
``{"type": "error", "error": {"type", "message"}}``.
"""

from __future__ import annotations

import json
from typing import Dict, Tuple

from runway.providers.gateway.protocol import ParsedResponse, PreparedRequest

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 1024


class AnthropicMessagesHandler:
    def build_request(
        self, *, base_url: str, api_key: str, upstream_model: str,
        messages: Tuple[Dict[str, str], ...], max_output_tokens: int,
    ) -> PreparedRequest:
        body = {
            "model": upstream_model,
            "max_tokens": max_output_tokens or _DEFAULT_MAX_TOKENS,
            "messages": [dict(item) for item in messages],
        }
        return PreparedRequest(
            url=f"{base_url}/messages",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
            },
            body=json.dumps(body).encode("utf-8"),
        )

    def parse_success(self, raw: bytes) -> ParsedResponse:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ParsedResponse(succeeded=False, error="malformed_response")
        if not isinstance(data, dict):
            return ParsedResponse(succeeded=False, error="malformed_response")

        reported_model = data.get("model") if isinstance(data.get("model"), str) else None
        request_id = data.get("id") if isinstance(data.get("id"), str) else None
        content = data.get("content")
        if not isinstance(content, list) or not content:
            return ParsedResponse(succeeded=False, error="malformed_response", reported_model=reported_model)

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        try:
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
        except (TypeError, ValueError):
            return ParsedResponse(succeeded=False, error="malformed_response", reported_model=reported_model)

        return ParsedResponse(
            succeeded=True, input_tokens=max(0, input_tokens), output_tokens=max(0, output_tokens),
            reported_model=reported_model, provider_request_id=request_id,
        )
