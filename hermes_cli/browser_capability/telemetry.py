"""Mission Control publication for sanitized browser activity."""

from __future__ import annotations

import time
import uuid
from typing import Any
from urllib.parse import urlparse

from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService

from .redaction import redact


def safe_hostname(arguments: dict[str, Any]) -> str | None:
    for key in ("url", "target", "href"):
        value = arguments.get(key)
        if isinstance(value, str):
            parsed = urlparse(value)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                return parsed.hostname.lower()
    return None


class BrowserTelemetryPublisher:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control

    def publish(
        self, *, project_id: str, task_id: str, tool_name: str, arguments: dict[str, Any],
        status: str, duration_ms: int, error_category: str | None,
    ) -> TelemetryEvent:
        payload = redact({
            "provider": "browseros",
            "location": "hostinger",
            "tool_name": tool_name,
            "target_hostname": safe_hostname(arguments),
            "result_status": status,
            "duration_ms": duration_ms,
            "error_category": error_category,
        })
        event = TelemetryEvent(
            event_id=f"browser_{uuid.uuid4().hex}",
            event_type="browser_execution_recorded",
            project_id=project_id,
            task_id=task_id,
            timestamp=int(time.time()),
            severity="info" if status == "success" else "warning",
            payload=payload,
        )
        return self._mission_control.append_event(event)
