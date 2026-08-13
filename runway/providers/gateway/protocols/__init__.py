"""Concrete protocol handlers. Import this package to register them all
against :func:`runway.providers.gateway.protocol.handler_for`.
"""

from __future__ import annotations

from runway.providers.gateway import protocol as _protocol
from runway.providers.gateway.models import GatewayProtocol
from runway.providers.gateway.protocols.anthropic_messages import AnthropicMessagesHandler
from runway.providers.gateway.protocols.openai_chat import OpenAIChatCompletionsHandler
from runway.providers.gateway.protocols.openai_responses import OpenAIResponsesHandler

_protocol.register_handler(GatewayProtocol.OPENAI_CHAT_COMPLETIONS, OpenAIChatCompletionsHandler())
_protocol.register_handler(GatewayProtocol.OPENAI_RESPONSES, OpenAIResponsesHandler())
_protocol.register_handler(GatewayProtocol.ANTHROPIC_MESSAGES, AnthropicMessagesHandler())

__all__ = ["AnthropicMessagesHandler", "OpenAIChatCompletionsHandler", "OpenAIResponsesHandler"]
