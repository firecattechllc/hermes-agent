"""The governed status vocabulary every generated claim must use.

Every statement this worker writes into the vault is tagged with exactly one
of these values. The vocabulary exists so a reader (human or another agent)
never has to guess what kind of evidence backs a claim -- "Deployed" always
means a live, currently-observed signal (a running systemd unit, a reachable
HTTP endpoint, a registry entry updated by a real heartbeat); "Implemented"
or "Configured" always means something weaker (code exists, a config file
is present) that must never be upgraded to "Deployed" or "Verified" on the
strength of source code, templates, or tests alone.
"""

from __future__ import annotations

from enum import Enum


class StatusValue(str, Enum):
    """Governed status tags. Order is significant: it is the fallback
    ordering used when multiple facts about the same subject must be
    collapsed into a single worst-case status (see
    :func:`most_conservative`)."""

    BLOCKED = "Blocked"
    DEGRADED = "Degraded"
    UNKNOWN = "Unknown"
    PLANNED = "Planned"
    IMPLEMENTED = "Implemented"
    CONFIGURED = "Configured"
    VERIFIED = "Verified"
    DEPLOYED = "Deployed"


# Conservatism rank: lower index sorts first when picking the "worse" of two
# statuses for the same subject. Blocked/Degraded outrank a rosier claim
# from a different source about the same thing -- a live failure signal
# always wins over an aspirational one.
_CONSERVATISM_RANK = {
    StatusValue.BLOCKED: 0,
    StatusValue.DEGRADED: 1,
    StatusValue.UNKNOWN: 2,
    StatusValue.PLANNED: 3,
    StatusValue.IMPLEMENTED: 4,
    StatusValue.CONFIGURED: 5,
    StatusValue.VERIFIED: 6,
    StatusValue.DEPLOYED: 7,
}

# Statuses that may only be asserted from a live, currently-observed signal
# (a running process, a reachable socket, a registry entry written by a real
# heartbeat) -- never from reading source code, a template, or a test file.
LIVE_ONLY_STATUSES = frozenset({StatusValue.VERIFIED, StatusValue.DEPLOYED})

# Statuses legitimately derivable from static inspection (source code
# present, a config file parsed and valid, a design intent recorded).
STATIC_DERIVABLE_STATUSES = frozenset(
    {StatusValue.IMPLEMENTED, StatusValue.CONFIGURED, StatusValue.PLANNED}
)


def most_conservative(statuses: "list[StatusValue] | tuple[StatusValue, ...]") -> StatusValue:
    """The most conservative (worst-case) status among several claims about
    the same subject. Empty input returns ``UNKNOWN`` -- absence of
    evidence is never treated as evidence of health."""
    if not statuses:
        return StatusValue.UNKNOWN
    return min(statuses, key=lambda s: _CONSERVATISM_RANK[s])
