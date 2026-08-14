"""Conservative browser audit redaction."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = re.compile(r"cookie|password|passwd|token|secret|authorization|session|bearer|api.?key", re.I)
_SECRET_TEXT = re.compile(
    r"(?i)(authorization|cookie|password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SECRET_KEYS.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return value
