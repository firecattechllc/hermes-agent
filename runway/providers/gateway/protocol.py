"""The protocol adapter contract (spec section 2): a generic adapter per
*wire format*, not per company. ``GatewayExecutionAdapter``
(``execution.py``) is the only thing that talks to
:class:`~runway.omniroute_port.ExecutionRequest`/``ExecutionResult`` — every
protocol handler here only ever sees primitive request parameters and raw
bytes, so provider-specific behavior has nowhere to hide except
:class:`~runway.providers.gateway.models.ChannelConfig` (base_url,
model_overrides) or a narrowly scoped normalizer, never a new adapter class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, Tuple

from runway.providers.errors import categorize_http_status
from runway.providers.gateway.models import GatewayProtocol

__all__ = [
    "PreparedRequest", "ParsedResponse", "ProtocolHandler",
    "categorize_http_status", "register_handler", "handler_for",
]


@dataclass(frozen=True)
class PreparedRequest:
    url: str
    headers: Dict[str, str]
    body: bytes


@dataclass(frozen=True)
class ParsedResponse:
    succeeded: bool
    input_tokens: int = 0
    output_tokens: int = 0
    reported_model: Optional[str] = None
    provider_request_id: Optional[str] = None
    #: An ExecutionFailureCategory value; only meaningful when succeeded=False.
    error: str = field(default="")


class ProtocolHandler(Protocol):
    def build_request(
        self, *, base_url: str, api_key: str, upstream_model: str,
        messages: Tuple[Dict[str, str], ...], max_output_tokens: int,
    ) -> PreparedRequest: ...

    def parse_success(self, raw: bytes) -> ParsedResponse: ...


_HANDLER_REGISTRY: Dict[GatewayProtocol, ProtocolHandler] = {}


def register_handler(protocol: GatewayProtocol, handler: ProtocolHandler) -> None:
    _HANDLER_REGISTRY[protocol] = handler


def handler_for(protocol: GatewayProtocol) -> ProtocolHandler:
    try:
        return _HANDLER_REGISTRY[protocol]
    except KeyError:
        raise NotImplementedError(f"no protocol handler registered for {protocol.value!r}") from None
