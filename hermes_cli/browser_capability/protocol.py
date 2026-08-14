"""Narrow browser capability contract used by Hermes callers."""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from .models import BrowserAvailability, BrowserHealth, BrowserTool, BrowserToolResult


class BrowserCapability(Protocol):
    async def availability(self) -> BrowserAvailability: ...
    async def health(self) -> BrowserHealth: ...
    async def list_tools(self) -> Sequence[BrowserTool]: ...
    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any], *, project_id: str, task_id: str
    ) -> BrowserToolResult: ...
