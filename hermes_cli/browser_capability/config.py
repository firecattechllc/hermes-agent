"""Configuration for the governed browser capability."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


class BrowserConfig(BaseModel):
    """Frozen configuration; BrowserOS is disabled unless explicitly enabled."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    provider: str = "browseros"
    transport: str = "streamable_http"
    endpoint: str = "http://127.0.0.1:9239/mcp"
    profile: str = "/var/lib/hermes-browseros/home"
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @model_validator(mode="after")
    def _supported_boundary(self) -> "BrowserConfig":
        if self.provider != "browseros" or self.transport != "streamable_http":
            raise ValueError("only BrowserOS Streamable HTTP is supported")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("BrowserOS MCP endpoint must be loopback HTTP")
        if parsed.path != "/mcp":
            raise ValueError("BrowserOS MCP endpoint path must be /mcp")
        return self

    @classmethod
    def from_env(cls) -> "BrowserConfig":
        return cls(
            enabled=_enabled("HERMES_BROWSER_ENABLED"),
            provider=os.environ.get("HERMES_BROWSER_PROVIDER", "browseros"),
            transport=os.environ.get("HERMES_BROWSER_TRANSPORT", "streamable_http"),
            endpoint=os.environ.get("HERMES_BROWSER_ENDPOINT", "http://127.0.0.1:9239/mcp"),
            profile=os.environ.get("HERMES_BROWSER_PROFILE", "/var/lib/hermes-browseros/home"),
            connect_timeout_seconds=float(os.environ.get("HERMES_BROWSER_CONNECT_TIMEOUT", "10")),
            tool_timeout_seconds=float(os.environ.get("HERMES_BROWSER_TOOL_TIMEOUT", "30")),
        )
