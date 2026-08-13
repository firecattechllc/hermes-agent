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


def clean_credential_ref(value: str) -> str:
    """Validate a credential *pointer* (e.g. ``"env:LAOZHANG_API_KEY"``,
    ``"keychain:runway/acme"``) — a reference to where a secret lives, never
    the secret itself.

    Deliberately not implemented via :func:`contains_sensitive_marker`:
    that marker list includes ``"api_key"``, which false-positives on the
    idiomatic pointer names every real credential_ref will actually use
    (``OPENAI_API_KEY``, ``LAOZHANG_API_KEY``, ...). Instead this rejects
    only shapes a raw secret value would actually take: a vendor key
    prefix, a literal ``Bearer`` header, or an inline ``key=value`` paste.
    """
    value = value.strip()
    if not value:
        raise ValueError("credential_ref must not be empty")
    if value.startswith(("sk-", "Bearer ", "Bearer\t")) or "=" in value:
        raise ValueError("credential_ref must be a pointer (e.g. 'env:NAME'), not a raw secret")
    return value
