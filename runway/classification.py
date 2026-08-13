"""Workload/data-sensitivity classification (spec section 6) and task taxonomy
(section 7).

Both are enforced in code, not left to comments. ``DataClassification`` feeds
directly into :mod:`runway.trust` eligibility checks; ``TaskType`` stays a
free-form validated identifier (matching the convention already used by
``hermes_cli.agent_roles.model_routing``) rather than a closed enum, so new
task types don't require a code change.
"""

from __future__ import annotations

from enum import IntEnum

from runway.identifiers import clean_identifier


class DataClassification(IntEnum):
    """Ordered from least to most sensitive. Ordering matters: eligibility
    checks in :mod:`runway.trust` use ``<=`` comparisons against a tier's
    ceiling, so inserting a new class requires picking its correct rank.
    """

    PUBLIC = 0
    SYNTHETIC = 1
    INTERNAL = 2
    PROPRIETARY = 3
    SENSITIVE = 4
    RESTRICTED = 5


#: Suggested task taxonomy (spec section 7). Not exhaustive and not enforced —
#: any string that passes :func:`normalize_task_type` is a valid task type.
#: Kept here so callers have a documented starting vocabulary consistent with
#: the shape of task types already used in ``hermes_cli.agent_roles``.
SUGGESTED_TASK_TYPES = (
    "simple_chat",
    "summarization",
    "extraction",
    "code_understanding",
    "code_generation",
    "bug_fix",
    "refactor",
    "test_generation",
    "repository_navigation",
    "deployment_reasoning",
    "security_review",
    "architecture",
    "multimodal",
    "embedding",
    "reranking",
    "tool_heavy_agent",
    "long_context",
    "low_latency",
    "batch_processing",
)


def normalize_task_type(value: str) -> str:
    return clean_identifier(value)
