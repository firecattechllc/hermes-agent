from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from hermes_cli.browser_capability import (
    BrowserCapabilityError,
    BrowserConfig,
    BrowserErrorCategory,
    BrowserOSMCPAdapter,
    BrowserState,
)
from hermes_cli.browser_capability.redaction import redact
from hermes_cli.browser_capability.telemetry import BrowserTelemetryPublisher
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


class FakeSession:
    def __init__(self, *, malformed=False, timeout=False, fail_initialize=False):
        self.malformed = malformed
        self.timeout = timeout
        self.fail_initialize = fail_initialize
        self.initialized = 0

    async def initialize(self):
        self.initialized += 1
        if self.fail_initialize:
            raise OSError("offline token=must-not-leak")

    async def list_tools(self):
        if self.timeout:
            await asyncio.sleep(1)
        if self.malformed:
            return SimpleNamespace(tools="not-a-list")
        return SimpleNamespace(tools=[SimpleNamespace(
            name="navigate_page", description="Navigate", inputSchema={"type": "object"}
        )])

    async def call_tool(self, name, arguments):
        return SimpleNamespace(content=[{"type": "text", "text": "Example Domain"}], isError=False)


def factory_for(session):
    @asynccontextmanager
    async def factory(_endpoint):
        yield session
    return factory


def enabled_config(**updates):
    return BrowserConfig(enabled=True, **updates)


@pytest.mark.asyncio
async def test_integration_disabled_by_default():
    adapter = BrowserOSMCPAdapter(BrowserConfig())
    assert (await adapter.availability()).state is BrowserState.DISABLED
    with pytest.raises(BrowserCapabilityError) as error:
        await adapter.list_tools()
    assert error.value.category is BrowserErrorCategory.DISABLED


@pytest.mark.asyncio
async def test_browseros_unavailable_is_sanitized():
    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory_for(FakeSession(fail_initialize=True)))
    availability = await adapter.availability()
    assert availability.state is BrowserState.OFFLINE
    assert availability.reason == "unavailable"
    assert "must-not-leak" not in str(availability)


@pytest.mark.asyncio
async def test_malformed_mcp_response():
    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory_for(FakeSession(malformed=True)))
    with pytest.raises(BrowserCapabilityError) as error:
        await adapter.list_tools()
    assert error.value.category is BrowserErrorCategory.PROTOCOL


@pytest.mark.asyncio
async def test_mcp_timeout():
    adapter = BrowserOSMCPAdapter(
        enabled_config(tool_timeout_seconds=0.01), session_factory=factory_for(FakeSession(timeout=True))
    )
    with pytest.raises(BrowserCapabilityError) as error:
        await adapter.list_tools()
    assert error.value.category is BrowserErrorCategory.TIMEOUT


@pytest.mark.asyncio
async def test_initialization_and_tool_discovery():
    session = FakeSession()
    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory_for(session))
    tools = await adapter.list_tools()
    assert session.initialized == 1
    assert [tool.name for tool in tools] == ["navigate_page"]


@pytest.mark.asyncio
async def test_runtime_status_projection_is_secret_free():
    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory_for(FakeSession()))
    status = (await adapter.health()).runtime_status()
    assert status == {
        "name": "Browser Worker",
        "provider": "browseros",
        "location": "hostinger",
        "state": "healthy",
        "mcp": "connected",
        "active_sessions": 0,
        "error_category": None,
    }


@pytest.mark.asyncio
async def test_safe_read_only_tool_execution_with_mocked_mcp():
    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory_for(FakeSession()))
    result = await adapter.execute_tool(
        "navigate_page", {"url": "https://example.com"}, project_id="p1", task_id="t1"
    )
    assert not result.is_error
    assert result.content[0]["text"] == "Example Domain"


@pytest.mark.asyncio
async def test_unknown_tool_fails_closed():
    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory_for(FakeSession()))
    with pytest.raises(BrowserCapabilityError) as error:
        await adapter.execute_tool("fill", {}, project_id="p1", task_id="t1")
    assert error.value.category is BrowserErrorCategory.TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_audit_event_generation_and_domain_only(tmp_path):
    service = MissionControlService(store=MissionControlStore(tmp_path))
    publisher = BrowserTelemetryPublisher(service)
    adapter = BrowserOSMCPAdapter(
        enabled_config(), telemetry=publisher, session_factory=factory_for(FakeSession())
    )
    await adapter.execute_tool(
        "navigate_page", {"url": "https://user:password@example.com/private?token=secret"},
        project_id="p1", task_id="t1",
    )
    event = service.get_events("p1")[0]
    assert event.event_type == "browser_execution_recorded"
    assert event.task_id == "t1"
    assert event.payload["target_hostname"] == "example.com"
    assert "secret" not in str(event.payload)
    assert "password" not in str(event.payload)


def test_secret_redaction_is_recursive():
    value = redact({"cookie": "abc", "nested": [{"auth_token": "xyz"}], "message": "password=hunter2"})
    assert value["cookie"] == "[REDACTED]"
    assert value["nested"][0]["auth_token"] == "[REDACTED]"
    assert "hunter2" not in value["message"]


@pytest.mark.asyncio
async def test_service_restart_reconnects_on_each_operation():
    sessions = [FakeSession(), FakeSession()]
    calls = 0

    @asynccontextmanager
    async def factory(_endpoint):
        nonlocal calls
        session = sessions[calls]
        calls += 1
        yield session

    adapter = BrowserOSMCPAdapter(enabled_config(), session_factory=factory)
    await adapter.list_tools()
    await adapter.list_tools()
    assert calls == 2
    assert all(session.initialized == 1 for session in sessions)


def test_non_loopback_endpoint_is_rejected():
    with pytest.raises(ValueError, match="loopback"):
        BrowserConfig(endpoint="http://0.0.0.0:9239/mcp")
