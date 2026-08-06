"""Desktop bridge entry point for governed Goose worker visibility.

Mission Control visibility only: this module reads the Goose worker's own
config/inspector/provider status and reports it. It performs no invocation,
no configuration mutation, and no approval decision of its own.
"""

from __future__ import annotations

from typing import Any

from sigil.ai.goose import GooseInspector, GooseWorkerConfig, GooseWorkerProvider


def goose_worker_visibility(
    config: GooseWorkerConfig | None = None,
    provider: GooseWorkerProvider | None = None,
) -> dict[str, Any]:
    """Read-only projection of Goose worker install/health/activity status.

    Never raises outward: a probe failure degrades to a reported
    ``unavailable`` state rather than breaking the Mission Control panel.
    """

    try:
        effective_config = config if config is not None else GooseWorkerConfig.from_environment()
        status = GooseInspector(effective_config).status()
    except Exception as error:  # noqa: BLE001 - degrade to a visible status, not a crash
        return {
            "available": False,
            "reason": f"goose_worker_status probe failed: {error}",
            "enabled": False,
            "installed": False,
            "health": "unavailable",
            "active_jobs": 0,
            "last_execution": None,
        }

    return {
        "available": True,
        "raw": status,
        "enabled": bool(status.get("enabled", False)),
        "installed": bool(status.get("installed", False)),
        "version": status.get("version"),
        "provider": status.get("provider"),
        "model": status.get("model"),
        "health": status.get("health"),
        "readiness": status.get("readiness"),
        "reason": status.get("reason"),
        "active_jobs": 0 if provider is None else provider.active_jobs,
        "last_execution": None if provider is None else provider.last_execution,
    }
