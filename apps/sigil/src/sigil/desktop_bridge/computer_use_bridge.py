"""Desktop bridge entry point for governed computer-use visibility.

Hermes add-on Phase B (Mission Control visibility). ``tools/computer_use/``
is the sole authoritative desktop/computer-use system (see
``docs/architecture/OLLAMA_ROUTING_BOUNDARY.md`` sibling decision in
``apps/sigil/src/sigil/desktop_bridge/bridge.py``'s module docstring for the
equivalent computer-use decision). This module only reads and reports that
system's own status function; it performs no computer-use action, no
approval decision, and no driver invocation of its own beyond the read-only
``cua-driver doctor``/``permissions status`` probes ``computer_use_status``
already performs.
"""

from __future__ import annotations

from typing import Any


def computer_use_visibility() -> dict[str, Any]:
    """Read-only projection of ``tools.computer_use.permissions.computer_use_status``.

    Never raises outward: a probe failure degrades to a reported
    ``unavailable`` state rather than breaking the Mission Control panel.
    """

    try:
        from tools.computer_use.permissions import computer_use_status

        status = computer_use_status()
    except Exception as error:  # noqa: BLE001 - degrade to a visible status, not a crash
        return {
            "available": False,
            "reason": f"computer_use_status probe failed: {error}",
            "driver_ready": False,
            "capability_gated": True,
            "execution_requires_approval": True,
        }

    return {
        "available": True,
        "raw": status,
        "driver_ready": bool(status.get("ready", False)),
        "capability_gated": True,
        "execution_requires_approval": True,
    }
