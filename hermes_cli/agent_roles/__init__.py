"""Specialized Agent Roles domain package."""

from .models import (
    AgentRole,
    Assignment,
    AssignmentHandoff,
    AssignmentOutcome,
    AssignmentResult,
    AssignmentStatus,
    BuiltinRole,
    HandoffReason,
    RoleCapability,
    RolePolicy,
    builtin_agent_roles,
    new_assignment_id,
    new_handoff_id,
    new_result_id,
    new_role_id,
)

__all__ = [
    "AgentRole",
    "Assignment",
    "AssignmentHandoff",
    "AssignmentOutcome",
    "AssignmentResult",
    "AssignmentStatus",
    "BuiltinRole",
    "HandoffReason",
    "RoleCapability",
    "RolePolicy",
    "builtin_agent_roles",
    "new_assignment_id",
    "new_handoff_id",
    "new_result_id",
    "new_role_id",
]
