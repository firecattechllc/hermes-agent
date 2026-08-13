"""OpenAI Responses API (``POST {base_url}/responses``) — distinct from Chat
Completions: request takes ``input`` (a list of role/content items) and
``max_output_tokens`` rather than ``messages``/``max_tokens``; response
carries an ``output`` array (of typed items) rather than ``choices``.
``Authorization: Bearer <key>``, same error/usage shape family as Chat
Completions (``usage.input_tokens`` / ``usage.output_tokens``,
``{"error": {...}}`` on failure).
"""

from __future__ import annotations

import json
from typing import Dict, Tuple

from runway.providers.gateway.protocol import ParsedResponse, PreparedRequest


class OpenAIResponsesHandler:
    def build_request(
        self, *, base_url: str, api_key: str, upstream_model: str,
        messages: Tuple[Dict[str, str], ...], max_output_tokens: int,
    ) -> PreparedRequest:
        body: dict = {
            "model": upstream_model,
            "input": [dict(item) for item in messages],
        }
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens
        return PreparedRequest(
            url=f"{base_url}/responses",
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
        output = data.get("output")
        if not isinstance(output, list) or not output:
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
