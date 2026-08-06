"""Desktop bridge entry points for Hermes WebUI discovery/health visibility.

Hermes add-on Phase B. Wraps ``sigil.hermes_webui_adapter`` for Mission
Control: evaluates each configured target's status without probing by
default (every :func:`sigil.hermes_webui_adapter.default_hermes_webui_targets`
entry starts ``enabled=False``), and only performs the real, bounded,
read-only HTTPS probe added in that module when a target has been
explicitly enabled by operator configuration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sigil.hermes_webui_adapter import (
    HermesWebUIValidationError,
    build_deep_link,
    default_hermes_webui_targets,
    evaluate_webui_status,
    probe_webui_target,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def hermes_webui_status() -> dict[str, Any]:
    """Evaluate every known target; probe only those explicitly enabled."""

    now = _now()
    statuses: list[dict[str, Any]] = []

    for target in default_hermes_webui_targets():
        probe = None
        if target.enabled:
            try:
                probe = probe_webui_target(target, now=now)
            except HermesWebUIValidationError:
                probe = None

        status = evaluate_webui_status(target, probe, now=now)
        payload = dict(status.__dict__ if hasattr(status, "__dict__") else {})
        payload = {
            "node_id": status.node_id,
            "display_name": status.display_name,
            "role": status.role.value,
            "state": status.state.value,
            "enabled": status.enabled,
            "observed_at": status.observed_at,
            "dashboard_version": status.dashboard_version,
            "worker_contract_compatible": status.worker_contract_compatible,
            "deep_link_available": status.deep_link_available,
            "reason": status.reason,
        }
        statuses.append(payload)

    return {"schema_version": 1, "targets": statuses}


def hermes_webui_deep_link(node_id: object, route: object) -> dict[str, Any]:
    if not isinstance(node_id, str) or not isinstance(route, str):
        raise HermesWebUIValidationError("node_id and route must be strings")

    for target in default_hermes_webui_targets():
        if target.node_id == node_id:
            return {"url": build_deep_link(target, route)}

    raise HermesWebUIValidationError(f"unknown Hermes WebUI node: {node_id}")
