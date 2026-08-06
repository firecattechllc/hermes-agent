"""Desktop bridge entry point for real, disabled-by-default Paperclip visibility.

Hermes add-on continuation run. Read-only Mission Control status only:
identity verification against a real, operator-configured Paperclip
instance via ``sigil.paperclip_transport``. Deliberately does not expose
``update_issue_status`` (the one mutating call in the transport module) as
a desktop bridge command -- wiring a mutating external call to an
unguarded IPC command would create a new approval-bypass path, which is
exactly what the existing Sigil governance boundary (see
``apps/sigil/src/sigil/ecosystem_boundary_certification.py``) exists to
prevent. That capability remains available in the transport module for a
future, explicitly approval-gated job-dispatch integration, not this one.
"""

from __future__ import annotations

import os
from typing import Any

from sigil.paperclip_transport import (
    PaperclipCredential,
    PaperclipHTTPError,
    PaperclipTransportConfig,
    PaperclipTransportError,
    get_current_agent_identity,
)

_TOKEN_ENV_VARS = ("PAPERCLIP_API_KEY", "SIGIL_PAPERCLIP_API_KEY")


def resolve_paperclip_credential() -> PaperclipCredential | None:
    """Resolve a real operator-supplied token from the environment, or ``None``.

    Never fabricates or defaults a credential. ``PAPERCLIP_API_KEY`` matches
    Paperclip's own documented env var name (see ``docs/api/authentication.md``
    in ``paperclipai/paperclip``); ``SIGIL_PAPERCLIP_API_KEY`` is the
    Sigil-namespaced alternative, checked second so an operator can scope a
    Sigil-specific key without colliding with a Paperclip agent runtime's own
    environment.
    """

    for var in _TOKEN_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return PaperclipCredential(token=value)
    return None


def _config_from_env() -> PaperclipTransportConfig:
    base_url = os.environ.get("SIGIL_PAPERCLIP_BASE_URL", "http://localhost:3100")
    enabled = os.environ.get("SIGIL_PAPERCLIP_ENABLED", "").strip().lower() in {"1", "true"}
    return PaperclipTransportConfig(enabled=enabled, base_url=base_url)


def paperclip_status() -> dict[str, Any]:
    """Real, read-only status projection for Mission Control.

    Never raises outward: every failure mode (disabled, no credential,
    unreachable, unauthenticated) degrades to a descriptive status dict so
    a Mission Control panel can always render something.
    """

    config = _config_from_env()

    if not config.enabled:
        return {
            "configured": False,
            "connected": False,
            "reason": "Paperclip integration is disabled by policy (set SIGIL_PAPERCLIP_ENABLED=true to enable).",
        }

    credential = resolve_paperclip_credential()
    if credential is None:
        return {
            "configured": False,
            "connected": False,
            "reason": "No Paperclip API credential is configured (PAPERCLIP_API_KEY / SIGIL_PAPERCLIP_API_KEY unset).",
        }

    try:
        identity = get_current_agent_identity(config, credential)
    except PaperclipHTTPError as error:
        return {
            "configured": True,
            "connected": False,
            "reason": f"Paperclip API returned HTTP {error.status}: {error.message}",
        }
    except PaperclipTransportError as error:
        return {
            "configured": True,
            "connected": False,
            "reason": f"Paperclip request failed: {error}",
        }

    return {
        "configured": True,
        "connected": True,
        "agent_id": identity.get("id"),
        "agent_name": identity.get("name"),
        "role": identity.get("role"),
        "base_url": config.base_url,
    }
