"""Normalized model/provider capability metadata (spec section 8)."""

from __future__ import annotations

from enum import Enum
from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field


class Capability(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    CODING = "coding"
    TOOL_CALLING = "tool_calling"
    PARALLEL_TOOL_CALLING = "parallel_tool_calling"
    VISION = "vision"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    VIDEO = "video"
    AUDIO = "audio"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    EMBEDDINGS = "embeddings"
    RERANKING = "reranking"
    STRUCTURED_OUTPUT = "structured_output"
    LONG_CONTEXT = "long_context"


class ApiProtocol(str, Enum):
    CHAT_COMPLETIONS = "chat_completions"
    MESSAGES = "messages"
    RESPONSES = "responses"
    EMBEDDINGS = "embeddings"
    CUSTOM = "custom"


class ModelCapabilityProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: Tuple[Capability, ...]
    max_context_tokens: int = Field(..., ge=1)
    max_output_tokens: int = Field(..., ge=1)
    protocols: Tuple[ApiProtocol, ...] = (ApiProtocol.CHAT_COMPLETIONS,)
    supports_tool_calling: bool = False
    supports_streaming: bool = True
    supports_cache: bool = False

    def supports_all(self, required: Tuple[Capability, ...]) -> bool:
        return set(required).issubset(self.capabilities)

    def fits_context(self, *, input_tokens: int, output_tokens: int) -> bool:
        return input_tokens <= self.max_context_tokens and output_tokens <= self.max_output_tokens
