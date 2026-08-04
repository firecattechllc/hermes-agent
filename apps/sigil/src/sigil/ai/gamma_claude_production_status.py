"""Truthful representation of the governed Claude subsystem's production status.

Sigil distinguishes four possible claims about any AI subsystem:

* implemented — the code exists;
* focused-tested — unit/integration tests exercise it in isolation;
* production-integrated — a production entry point (routing, registry,
  runtime wiring) actually references it;
* production-enabled — it is integrated *and* configured on.

The governed Claude provider (:mod:`sigil.ai.claude`) is implemented and
focus-tested, but as of this module it is not referenced by any production
entry point (desktop bridge runtime, registry wiring, routing tables). This
module makes that state an explicit, derived value instead of leaving Gamma
certification silent (and therefore free to be read as an implicit "yes").
"""

from __future__ import annotations

from enum import Enum


class GammaClaudeProductionStatus(str, Enum):
    """Where the governed Claude subsystem stands relative to production."""

    NOT_PRODUCTION_INTEGRATED = "not_production_integrated"
    PRODUCTION_INTEGRATED_DISABLED = "production_integrated_disabled"
    PRODUCTION_INTEGRATED_ENABLED = "production_integrated_enabled"


def gamma_claude_production_status(
    *,
    wired_into_production_runtime: bool,
    config_enabled: bool,
) -> GammaClaudeProductionStatus:
    """Derive the truthful production status from two independent facts.

    Both inputs must be supplied by the caller from real, checkable state
    (e.g. a source-inspection check for wiring, ``ClaudeConfig.enabled`` for
    the flag) rather than asserted — this function never defaults either
    input, so a caller cannot claim "enabled" without also claiming, and
    therefore being answerable for, "integrated".
    """

    if not wired_into_production_runtime:
        return GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED
    if not config_enabled:
        return GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_DISABLED
    return GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED


def claude_production_integrated(status: GammaClaudeProductionStatus) -> bool:
    return status != GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED


def claude_production_enabled(status: GammaClaudeProductionStatus) -> bool:
    return status == GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED
