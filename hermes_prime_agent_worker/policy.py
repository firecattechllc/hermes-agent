"""Governance decisions for the Prime Agent worker adapter.

Every non-admitted decision carries a non-empty, closed-vocabulary
``reason_codes`` tuple -- mirroring ``hermes_cli.prime.admission``'s
convention -- and every applicable violation is accumulated rather than
short-circuited, so a denial always explains everything wrong, not just
the first check that failed.

This module makes decisions; it never executes anything. See
:mod:`hermes_prime_agent_worker.proc` for the only place a Prime Agent
subprocess is actually started, and :mod:`hermes_prime_agent_worker.sessions`
for the orchestration that calls this module first and refuses to proceed
on any denial.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from hermes_prime_agent_worker.config import (
    OWN_SYSTEMD_UNIT_NAME,
    PrimeAgentWorkerConfig,
)

# Closed reason-code vocabulary. A caller must not invent new strings here;
# add a new constant instead so every denial reason is enumerable.
WORKSPACE_NOT_ALLOWLISTED = "workspace_not_allowlisted"
MUTATION_NOT_APPROVED = "mutation_not_approved"
NETWORK_NOT_APPROVED = "network_endpoint_not_approved"
PACKAGE_INSTALL_NOT_APPROVED = "package_install_not_approved"
PROVIDER_NOT_ACTIVE = "provider_not_active"
TOOL_NOT_ALLOWLISTED = "tool_not_allowlisted"
PRIVILEGED_COMMAND_DENIED = "privileged_command_denied"
TURNS_BUDGET_EXCEEDED = "turns_budget_exceeded"
TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
TIMEOUT_BUDGET_EXCEEDED = "timeout_budget_exceeded"
CONCURRENT_SESSION_LIMIT_EXCEEDED = "concurrent_session_limit_exceeded"
COOLDOWN_ACTIVE = "cooldown_active"
KILL_SWITCH_ACTIVE = "kill_switch_active"
SELF_UNIT_MUTATION_DENIED = "self_unit_mutation_denied"
GIT_MUTATION_DENIED = "git_mutation_denied"
UNKNOWN_TOOL_REQUESTED = "unknown_tool_requested"

_PRIVILEGED_COMMAND_MARKERS: Tuple[str, ...] = (
    "sudo",
    "su ",
    "su-",
    "doas",
    "passwd",
    "systemctl enable",
    "systemctl edit",
    "systemctl disable",
    "usermod",
    "useradd",
    "userdel",
    "visudo",
    "chmod 777",
    "chown root",
    "docker ",
    "mount ",
    "umount ",
    "iptables",
    "nft ",
    "/etc/shadow",
    "/etc/sudoers",
    "ssh-keygen",
    "authorized_keys",
)

_GIT_MUTATION_MARKERS: Tuple[str, ...] = (
    "git merge",
    "git tag",
    "git push",
    "git commit",
    "git rebase",
    "gh pr merge",
    "gh release",
    "git branch -d",
    "git branch -D",
)


def is_privileged_command(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _PRIVILEGED_COMMAND_MARKERS)


def is_git_mutation_command(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _GIT_MUTATION_MARKERS)


def references_own_systemd_unit(text: str) -> bool:
    return OWN_SYSTEMD_UNIT_NAME in text or "hermes-prime-agent-worker" in text.lower()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    permitted: bool
    reason_codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.permitted and not self.reason_codes:
            raise ValueError(
                "a non-permitted decision requires at least one reason code"
            )
        if self.permitted and self.reason_codes:
            raise ValueError("a permitted decision must not carry reason codes")

    @classmethod
    def admit(cls) -> "PolicyDecision":
        return cls(permitted=True, reason_codes=())

    @classmethod
    def deny(cls, *reason_codes: str) -> "PolicyDecision":
        return cls(permitted=False, reason_codes=tuple(reason_codes))


def evaluate_task_request(
    config: PrimeAgentWorkerConfig,
    *,
    workspace: Path,
    task_text: str,
    requested_tools: Tuple[str, ...] = (),
    mutation_approved: bool = False,
    network_approved: bool = False,
    package_install_approved: bool = False,
    gate_commands: Tuple[str, ...] = (),
    active_session_count: int = 0,
    consecutive_failures: int = 0,
    seconds_since_last_failure: Optional[float] = None,
    kill_switch_active: bool = False,
) -> PolicyDecision:
    """Evaluate whether a bounded task run may proceed. Fails closed: any
    unmet condition accumulates a reason code rather than short-circuiting,
    so operators see every problem at once."""

    reasons: list[str] = []

    if kill_switch_active:
        reasons.append(KILL_SWITCH_ACTIVE)

    if not config.is_within_allowlist(workspace):
        reasons.append(WORKSPACE_NOT_ALLOWLISTED)

    if not config.provider_active:
        reasons.append(PROVIDER_NOT_ACTIVE)

    permitted_tools = set(config.allowed_tools)
    if mutation_approved:
        permitted_tools |= set(config.mutation_tools)
    unknown = [t for t in requested_tools if t not in permitted_tools]
    if unknown:
        if any(
            t not in set(config.allowed_tools) | set(config.mutation_tools)
            for t in unknown
        ):
            reasons.append(UNKNOWN_TOOL_REQUESTED)
        else:
            reasons.append(TOOL_NOT_ALLOWLISTED)
        if set(requested_tools) & set(config.mutation_tools) and not mutation_approved:
            reasons.append(MUTATION_NOT_APPROVED)

    if is_privileged_command(task_text):
        reasons.append(PRIVILEGED_COMMAND_DENIED)
    if references_own_systemd_unit(task_text):
        reasons.append(SELF_UNIT_MUTATION_DENIED)
    if is_git_mutation_command(task_text):
        reasons.append(GIT_MUTATION_DENIED)

    for gate in gate_commands:
        if is_privileged_command(gate):
            reasons.append(PRIVILEGED_COMMAND_DENIED)
        if is_git_mutation_command(gate):
            reasons.append(GIT_MUTATION_DENIED)

    if active_session_count >= config.max_concurrent_sessions:
        reasons.append(CONCURRENT_SESSION_LIMIT_EXCEEDED)

    if (
        consecutive_failures >= config.max_consecutive_failures_before_cooldown
        and seconds_since_last_failure is not None
        and seconds_since_last_failure < config.cooldown_seconds_after_failure
    ):
        reasons.append(COOLDOWN_ACTIVE)

    # De-duplicate while preserving first-seen order.
    seen: dict[str, None] = {}
    for item in reasons:
        seen.setdefault(item, None)
    reasons = list(seen.keys())

    if reasons:
        return PolicyDecision.deny(*reasons)
    return PolicyDecision.admit()


def evaluate_doctor_fix(*, requested_fix: bool, fix_approved: bool) -> PolicyDecision:
    """``prime-agent doctor --fix`` mutates local daemon/socket state.
    Requires explicit, separately-tracked approval -- never runs just
    because the caller asked for it."""
    if not requested_fix:
        return PolicyDecision.admit()
    if not fix_approved:
        return PolicyDecision.deny(MUTATION_NOT_APPROVED)
    return PolicyDecision.admit()
