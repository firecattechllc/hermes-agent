"""Small, dependency-free validation helpers shared across Runway's domain models.

Deliberately duplicated (not imported) from ``hermes_cli.agent_roles.model_routing``'s
private helpers: Runway must remain importable and testable without depending on
any other subsystem, certified or otherwise. The rules are identical by design.
"""

from __future__ import annotations

import hashlib
import json
import re

_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._:@-]{0,159}")

_SENSITIVE_MARKERS = (
    "api_key", "api-key", "authorization", "bearer ", "password",
    "private_key", "secret", "token", "credential",
)


def clean_identifier(value: str) -> str:
    """Validate and normalize a stable lowercase identifier (ids, enum values)."""
    value = value.strip()
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Runway identifiers must be stable lowercase identifiers, got {value!r}")
    return value


def clean_label(value: str) -> str:
    """Validate a human-readable label, rejecting anything shaped like a secret."""
    value = value.strip()
    lowered = value.lower()
    if not value or any(marker in lowered for marker in _SENSITIVE_MARKERS):
        raise ValueError("Runway labels must not contain sensitive configuration")
    return value


def digest(value: object) -> str:
    """Deterministic content hash used for fingerprints and idempotency keys."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def contains_sensitive_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)
