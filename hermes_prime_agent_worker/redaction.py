"""Secret redaction applied at every boundary that might echo Prime Agent's
own output, arguments, or environment back into evidence, logs, or CLI
output. Mirrors ``hermes_docs_worker.redaction``'s pattern-based approach.
"""

from __future__ import annotations

import re
from typing import Tuple

_SECRET_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)api[_-]?key[\"'=:\s]+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)authorization:\s*\S+"),
    re.compile(r"(?i)(secret|token|password)[\"'=:\s]+[A-Za-z0-9._\-]{8,}"),
)

_REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """Mask obvious secret-shaped substrings. Never raises; a false-negative
    is possible (this is defense in depth, not the primary control -- the
    primary control is that Prime Agent never receives real credentials to
    begin with, see proc.py's minimal environment allowlist)."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def assert_redacted(text: str) -> None:
    if contains_secret(text):
        raise ValueError("value still contains an unredacted secret-shaped substring")
