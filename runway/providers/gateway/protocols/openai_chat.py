"""OpenAI-compatible Chat Completions (``POST {base_url}/chat/completions``).

Confirmed shape (this is what LaoZhang and most OpenAI-compatible gateways
speak): ``Authorization: Bearer <key>``, request body
``{model, messages, stream, max_tokens}``, response
``{id, model, choices: [...], usage: {prompt_tokens, completion_tokens}}``,
errors as HTTP status + ``{"error": {"message", "type", "code"}}``.
"""

from __future__ import annotations

import json
from typing import Dict, Tuple

from runway.providers.gateway.protocol import ParsedResponse, PreparedRequest


class OpenAIChatCompletionsHandler:
    def build_request(
        self, *, base_url: str, api_key: str, upstream_model: str,
        messages: Tuple[Dict[str, str], ...], max_output_tokens: int,
    ) -> PreparedRequest:
        body: dict = {
            "model": upstream_model,
            "messages": [dict(item) for item in messages],
            "stream": False,
        }
        if max_output_tokens:
            body["max_tokens"] = max_output_tokens
        return PreparedRequest(
            url=f"{base_url}/chat/completions",
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
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
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ParsedResponse(succeeded=False, error="malformed_response", reported_model=reported_model)

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        try:
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (TypeError, ValueError):
            return ParsedResponse(succeeded=False, error="malformed_response", reported_model=reported_model)

        return ParsedResponse(
            succeeded=True, input_tokens=max(0, input_tokens), output_tokens=max(0, output_tokens),
            reported_model=reported_model, provider_request_id=request_id,
        )
