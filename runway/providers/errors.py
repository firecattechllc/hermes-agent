"""Normalized execution failure categories.

``runway.omniroute_port.ExecutionResult.error`` is a plain string field — no
protocol change was needed to normalize errors, only a controlled vocabulary
for what goes in it. This maps directly onto the failure booleans already on
``runway.outcomes.TaskOutcome`` (``network_failure``, ``provider_error``,
``validation_failure``) via :func:`to_task_outcome_flags`, so a caller
recording history doesn't need provider-specific error-parsing logic.
"""

from __future__ import annotations

from enum import Enum


class ExecutionFailureCategory(str, Enum):
    NETWORK_FAILURE = "network_failure"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_RESPONSE = "malformed_response"
    EXECUTION_NOT_AUTHORIZED = "execution_not_authorized"


#: (network_failure, provider_error, validation_failure) — matches the three
#: TaskOutcome booleans exactly.
_OUTCOME_FLAGS: dict[ExecutionFailureCategory, tuple[bool, bool, bool]] = {
    ExecutionFailureCategory.NETWORK_FAILURE: (True, False, False),
    ExecutionFailureCategory.TIMEOUT: (True, False, False),
    ExecutionFailureCategory.RATE_LIMITED: (False, True, False),
    ExecutionFailureCategory.AUTH_FAILED: (False, True, False),
    ExecutionFailureCategory.INVALID_REQUEST: (False, False, True),
    ExecutionFailureCategory.PROVIDER_ERROR: (False, True, False),
    ExecutionFailureCategory.MALFORMED_RESPONSE: (False, True, False),
    ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED: (False, False, True),
}


def categorize_http_status(status: int) -> ExecutionFailureCategory:
    """Shared HTTP-status -> failure-category mapping, reused by every
    protocol handler in ``runway.providers.gateway.protocols`` so the
    OpenAI-compatible, OpenAI Responses, and Anthropic Messages adapters
    don't each reinvent it. All three protocols use the same status-code
    vocabulary (400/401/403/404/413/429/5xx); 529 (Anthropic's "overloaded")
    is treated the same as any other server error.
    """
    if status in (401, 403):
        return ExecutionFailureCategory.AUTH_FAILED
    if status == 429:
        return ExecutionFailureCategory.RATE_LIMITED
    if status in (400, 404, 413):
        return ExecutionFailureCategory.INVALID_REQUEST
    return ExecutionFailureCategory.PROVIDER_ERROR


def to_task_outcome_flags(error: str) -> dict:
    """Map an :class:`~runway.omniroute_port.ExecutionResult.error` string to
    the ``network_failure`` / ``provider_error`` / ``validation_failure``
    kwargs for constructing a ``runway.outcomes.TaskOutcome``. Unknown
    strings (including ``""`` for success) map to all-``False`` — a category
    this module doesn't recognize is never assumed to be a network failure.
    """
    try:
        category = ExecutionFailureCategory(error)
    except ValueError:
        return {"network_failure": False, "provider_error": False, "validation_failure": False}
    network_failure, provider_error, validation_failure = _OUTCOME_FLAGS[category]
    return {
        "network_failure": network_failure,
        "provider_error": provider_error,
        "validation_failure": validation_failure,
    }
