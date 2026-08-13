"""Model identity / canary framework (spec section 5).

Detects silent model substitution, degraded tool behavior, reduced
capability, and broken structured-output/context claims — by running a small
deterministic suite against a provider client. In this phase the only client
implementation is :class:`DeterministicFakeProvider`; nothing here ever
contacts a real provider. Swapping in a real client later requires no change
to :func:`run_canary_suite` — that's the point of the ``ModelClient`` protocol.
"""

from __future__ import annotations

from typing import Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field

from runway.capabilities import Capability, ModelCapabilityProfile
from runway.registry import CanaryStatus


class ModelClient(Protocol):
    """Narrow, provider-agnostic contract a canary suite can run against."""

    def identity_fingerprint(self) -> str: ...
    def supports_tool_calling(self) -> bool: ...
    def supports_structured_output(self) -> bool: ...
    def context_limit(self) -> int: ...
    def structured_response(self, schema_id: str) -> dict: ...
    def call_tool(self, name: str, args: dict) -> dict: ...


class DeterministicFakeProvider:
    """A configurable, deterministic fake used for both "healthy provider"
    and "drifted/substituted provider" test scenarios. Never touches a
    network.
    """

    def __init__(
        self,
        *,
        identity_fingerprint: str,
        context_limit: int,
        supports_tool_calling: bool = True,
        supports_structured_output: bool = True,
        tool_call_should_error: bool = False,
        structured_response_missing_fields: bool = False,
    ) -> None:
        self._identity_fingerprint = identity_fingerprint
        self._context_limit = context_limit
        self._supports_tool_calling = supports_tool_calling
        self._supports_structured_output = supports_structured_output
        self._tool_call_should_error = tool_call_should_error
        self._structured_response_missing_fields = structured_response_missing_fields

    def identity_fingerprint(self) -> str:
        return self._identity_fingerprint

    def supports_tool_calling(self) -> bool:
        return self._supports_tool_calling

    def supports_structured_output(self) -> bool:
        return self._supports_structured_output

    def context_limit(self) -> int:
        return self._context_limit

    def structured_response(self, schema_id: str) -> dict:
        if self._structured_response_missing_fields:
            return {}
        return {"schema_id": schema_id, "value": "ok"}

    def call_tool(self, name: str, args: dict) -> dict:
        if self._tool_call_should_error:
            return {"error": "tool_call_failed"}
        return {"tool": name, "result": "ok"}


class CanaryCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    detail: str = ""


class CanaryOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_key: str
    suite_version: int = Field(..., ge=0)
    executed_at: int = Field(..., ge=0)
    cases: Tuple[CanaryCaseResult, ...]
    suspected_identity_mismatch: bool

    @property
    def pass_rate(self) -> int:
        if not self.cases:
            return 100
        passed = sum(1 for item in self.cases if item.passed)
        return (passed * 100) // len(self.cases)

    @property
    def status(self) -> CanaryStatus:
        return CanaryStatus.PASSED if self.pass_rate == 100 else CanaryStatus.FAILED

    @property
    def failure_reason(self) -> Optional[str]:
        failures = [f"{item.name}: {item.detail}" for item in self.cases if not item.passed]
        return "; ".join(failures) if failures else None


CANARY_SUITE_VERSION = 1


def run_canary_suite(
    client: ModelClient,
    *,
    model_key: str,
    expected: ModelCapabilityProfile,
    expected_identity_fingerprint: Optional[str],
    now: int,
    suite_version: int = CANARY_SUITE_VERSION,
) -> CanaryOutcome:
    cases: list[CanaryCaseResult] = []
    mismatch = False

    if expected_identity_fingerprint is not None:
        actual = client.identity_fingerprint()
        matched = actual == expected_identity_fingerprint
        cases.append(CanaryCaseResult(
            name="identity_fingerprint", passed=matched,
            detail="" if matched else f"expected {expected_identity_fingerprint!r}, got {actual!r}",
        ))
        mismatch = not matched

    cases.append(CanaryCaseResult(
        name="context_window",
        passed=client.context_limit() >= expected.max_context_tokens,
        detail="" if client.context_limit() >= expected.max_context_tokens
        else f"advertised {expected.max_context_tokens}, probed {client.context_limit()}",
    ))

    if Capability.STRUCTURED_OUTPUT in expected.capabilities:
        response = client.structured_response("runway_canary_probe")
        ok = client.supports_structured_output() and bool(response.get("value"))
        cases.append(CanaryCaseResult(
            name="structured_output", passed=ok,
            detail="" if ok else "structured output missing expected fields",
        ))

    if Capability.TOOL_CALLING in expected.capabilities:
        result = client.call_tool("runway_canary_probe", {})
        ok = client.supports_tool_calling() and "error" not in result
        cases.append(CanaryCaseResult(
            name="tool_calling", passed=ok,
            detail="" if ok else str(result.get("error", "tool call unsupported")),
        ))

    return CanaryOutcome(
        model_key=model_key, suite_version=suite_version, executed_at=now,
        cases=tuple(cases), suspected_identity_mismatch=mismatch,
    )
