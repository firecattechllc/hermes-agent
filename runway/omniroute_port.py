"""The narrow execution contract Runway will eventually hand a
:class:`~runway.scoring.RouteDecision` to (spec section 17: "Runway = decide,
OmniRoute = execute").

Reconnaissance found no single stable "OmniRoute" execution substrate to
bind to yet: the code named ``omniroute_*`` lives inside ``hermes_cli/prime/``
(certified, protected) and only exists on an unmerged branch; the real
production execution path is ``providers/`` + ``plugins/model-providers/``.
Rather than guess which one this should eventually call, Runway defines its
own tiny ``ExecutionPort`` protocol and depends on nothing but that — a real
adapter (whichever substrate it turns out to wrap) is a separate,
later change that implements this protocol without touching anything here.

In this phase only :class:`FakeExecutionAdapter` exists, and
:data:`runway.flags.RunwayFeatureFlags.external_execution_enabled` is
hard-pinned to ``False`` so there is no path to a real adapter even if one
were added.

``messages`` / ``reported_model`` / ``provider_request_id`` (added when the
first real adapter, ``runway.providers.laozhang``, was commissioned): a real
adapter cannot execute a chat completion with no content, and Phase E/G
evidence requirements need the provider-echoed model id and request id
somewhere to land. All three are optional and default to empty/``None`` —
:class:`FakeExecutionAdapter` and every existing caller are unaffected.
``messages`` is never populated by :class:`~runway.router.RunwayRouter` or
:class:`~runway.scoring.RouteScorer` (which only ever construct
``RouteDecision``, never ``ExecutionRequest``), and ``runway.telemetry``
already rejects any event payload containing a ``messages`` key.
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field

from runway.pricing import UsageEstimate


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    provider_id: str
    model_key: str
    usage_estimate: UsageEstimate
    messages: Tuple[Dict[str, str], ...] = Field(
        default=(),
        description=(
            "Real request content for adapters that need it (e.g. a live HTTP "
            "provider). Empty for pure decision/simulation flows."
        ),
    )


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    succeeded: bool
    actual_input_tokens: int = Field(default=0, ge=0)
    actual_output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    error: str = ""
    reported_model: Optional[str] = Field(
        default=None,
        description="Provider-echoed model identifier — advisory only, not proof of identity.",
    )
    provider_request_id: Optional[str] = Field(default=None)


class ExecutionPort(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


class FakeExecutionAdapter:
    """Deterministic in-process fake. Never opens a socket. Configured per
    model_key with a fixed outcome so tests and the benchmark (section 25)
    can simulate success/failure/latency distributions reproducibly.
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, ExecutionResult] = {}

    def configure(self, model_key: str, result: ExecutionResult) -> None:
        self._outcomes[model_key] = result

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        configured = self._outcomes.get(request.model_key)
        if configured is None:
            return ExecutionResult(decision_id=request.decision_id, succeeded=False, error="unconfigured_fake_route")
        return configured.model_copy(update={"decision_id": request.decision_id})
