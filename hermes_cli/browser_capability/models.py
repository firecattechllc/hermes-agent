"""Typed state, results, and fail-closed errors for browser access."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrowserState(str, Enum):
    DISABLED = "disabled"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    HEALTHY = "healthy"


class BrowserErrorCategory(str, Enum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_FAILED = "tool_failed"


class BrowserCapabilityError(RuntimeError):
    def __init__(self, category: BrowserErrorCategory, message: str) -> None:
        self.category = category
        super().__init__(message)


class BrowserAvailability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    available: bool
    state: BrowserState
    reason: str | None = None


class BrowserHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str = "browseros"
    location: str = "hostinger"
    state: BrowserState
    mcp_connected: bool
    active_sessions: int = Field(default=0, ge=0)
    error_category: BrowserErrorCategory | None = None

    def runtime_status(self) -> dict[str, Any]:
        """Stable, secret-free projection for Hermes/Sigil runtime snapshots."""
        return {
            "name": "Browser Worker",
            "provider": self.provider,
            "location": self.location,
            "state": self.state.value,
            "mcp": "connected" if self.mcp_connected else "disconnected",
            "active_sessions": self.active_sessions,
            "error_category": self.error_category.value if self.error_category else None,
        }


class BrowserTool(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class BrowserToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool_name: str
    content: list[dict[str, Any]] = Field(default_factory=list)
    is_error: bool = False
