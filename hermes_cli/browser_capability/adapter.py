"""BrowserOS adapter using its loopback Streamable HTTP MCP endpoint."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from .config import BrowserConfig
from .models import (
    BrowserAvailability, BrowserCapabilityError, BrowserErrorCategory, BrowserHealth,
    BrowserState, BrowserTool, BrowserToolResult,
)
from .telemetry import BrowserTelemetryPublisher


@asynccontextmanager
async def _mcp_session(endpoint: str) -> AsyncIterator[Any]:
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise BrowserCapabilityError(BrowserErrorCategory.UNAVAILABLE, "MCP client support is not installed") from exc
    async with streamable_http_client(endpoint) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            yield session


class BrowserOSMCPAdapter:
    def __init__(
        self, config: BrowserConfig, *, telemetry: BrowserTelemetryPublisher | None = None,
        session_factory: Callable[[str], Any] = _mcp_session,
    ) -> None:
        self._config = config
        self._telemetry = telemetry
        self._session_factory = session_factory
        self._connected = False
        self._active_sessions = 0
        self._last_error: BrowserErrorCategory | None = None

    def _require_enabled(self) -> None:
        if not self._config.enabled:
            raise BrowserCapabilityError(BrowserErrorCategory.DISABLED, "browser capability is disabled")

    async def _run(self, operation: Callable[[Any], Any], timeout: float) -> Any:
        self._require_enabled()
        try:
            async with asyncio.timeout(self._config.connect_timeout_seconds):
                async with self._session_factory(self._config.endpoint) as session:
                    await session.initialize()
                    self._connected = True
                    return await asyncio.wait_for(operation(session), timeout=timeout)
        except BrowserCapabilityError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            self._connected = False
            self._last_error = BrowserErrorCategory.TIMEOUT
            raise BrowserCapabilityError(BrowserErrorCategory.TIMEOUT, "BrowserOS MCP operation timed out") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            self._connected = False
            self._last_error = BrowserErrorCategory.PROTOCOL
            raise BrowserCapabilityError(BrowserErrorCategory.PROTOCOL, "BrowserOS returned a malformed MCP response") from exc
        except Exception as exc:
            self._connected = False
            self._last_error = BrowserErrorCategory.UNAVAILABLE
            raise BrowserCapabilityError(BrowserErrorCategory.UNAVAILABLE, "BrowserOS MCP is unavailable") from exc

    async def availability(self) -> BrowserAvailability:
        if not self._config.enabled:
            return BrowserAvailability(available=False, state=BrowserState.DISABLED, reason="disabled_by_configuration")
        try:
            await self.list_tools()
            return BrowserAvailability(available=True, state=BrowserState.HEALTHY)
        except BrowserCapabilityError as exc:
            return BrowserAvailability(available=False, state=BrowserState.OFFLINE, reason=exc.category.value)

    async def health(self) -> BrowserHealth:
        availability = await self.availability()
        return BrowserHealth(
            state=availability.state,
            mcp_connected=self._connected and availability.available,
            active_sessions=self._active_sessions,
            error_category=self._last_error,
        )

    async def list_tools(self) -> tuple[BrowserTool, ...]:
        async def operation(session: Any) -> tuple[BrowserTool, ...]:
            response = await session.list_tools()
            tools = response.tools
            if not isinstance(tools, list):
                raise ValueError("tools must be a list")
            return tuple(BrowserTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
            ) for tool in tools)
        return await self._run(operation, self._config.tool_timeout_seconds)

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any], *, project_id: str, task_id: str,
    ) -> BrowserToolResult:
        started = time.monotonic()
        status = "failed"
        category: BrowserErrorCategory | None = None
        self._active_sessions += 1
        try:
            tools = await self.list_tools()
            if tool_name not in {tool.name for tool in tools}:
                raise BrowserCapabilityError(BrowserErrorCategory.TOOL_NOT_FOUND, "requested BrowserOS tool was not discovered")

            async def operation(session: Any) -> BrowserToolResult:
                response = await session.call_tool(tool_name, arguments=arguments)
                content = []
                for item in response.content:
                    if hasattr(item, "model_dump"):
                        content.append(item.model_dump(mode="json"))
                    elif isinstance(item, dict):
                        content.append(item)
                    else:
                        raise ValueError("unsupported tool content")
                return BrowserToolResult(tool_name=tool_name, content=content, is_error=bool(response.isError))

            result = await self._run(operation, self._config.tool_timeout_seconds)
            if result.is_error:
                raise BrowserCapabilityError(BrowserErrorCategory.TOOL_FAILED, "BrowserOS tool reported failure")
            status = "success"
            return result
        except BrowserCapabilityError as exc:
            category = exc.category
            raise
        finally:
            self._active_sessions -= 1
            if self._telemetry:
                self._telemetry.publish(
                    project_id=project_id, task_id=task_id, tool_name=tool_name,
                    arguments=arguments, status=status,
                    duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_category=category.value if category else None,
                )
