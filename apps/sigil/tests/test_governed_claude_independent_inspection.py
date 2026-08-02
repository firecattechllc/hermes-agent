from __future__ import annotations

import json

import pytest

from sigil.ai import (
    CLAUDE_PROVIDER_ID,
    Capability,
    ClaudeInspectionFailure,
    ClaudeInspectionRequest,
    ClaudeInspectionValidationError,
    ExecutionLocation,
    GovernedClaudeInspectionService,
    ProviderFailure,
    ProviderFailureClass,
    ProviderIdentity,
    ProviderResult,
    build_invocation_evidence,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-02T20:45:00Z"
LATER = "2026-08-02T20:45:01Z"


class FakeClaudeProvider:
    model_id = "claude-sonnet-governed"
    model_version = "gamma-foundation-v1"
    capabilities = frozenset({Capability.REASONING})
    request_timeout_ms = 60_000

    def __init__(
        self,
        *,
        content: str | None = None,
        failure: ProviderFailureClass | None = None,
    ) -> None:
        self.identity = ProviderIdentity(
            CLAUDE_PROVIDER_ID,
            ExecutionLocation.EXTERNAL,
        )
        self.content = content or json.dumps(
            {
                "findings": [
                    {
                        "finding_id": "finding-001",
                        "severity": "medium",
                        "category": "routing",
                        "summary": "External fallback requires explicit admission.",
                        "evidence_references": [DIGEST_B],
                        "recommendation": "Retain the explicit admission policy.",
                    }
                ],
                "limitations": ["Inspection used sanitized bounded material only."],
            }
        )
        self.failure = failure
        self.calls = []

    def invoke(self, invocation):
        self.calls.append(invocation)
        failure = (
            None
            if self.failure is None
            else ProviderFailure(self.failure, "failed safely", False)
        )
        output = None if failure is not None else {"content": self.content}
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=self.identity.provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=self.identity.execution_location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=failure is None,
            failure_classification=None if failure is None else failure.classification.value,
            input_payload={"prompt_digest": DIGEST_A},
            output_payload=None if output is None else {"content_digest": DIGEST_B},
            provider_metadata=(("adapter", "fake-claude-inspection"),),
        )
        return ProviderResult(output=output, failure=failure, evidence=evidence)


def request(**changes) -> ClaudeInspectionRequest:
    values = {
        "inspection_id": "gamma-stage4-inspection",
        "task_correlation_id": "gamma-stage4-task",
        "target_revision": "d576352e6",
        "target_digest": DIGEST_A,
        "evidence_digests": (DIGEST_B,),
        "inspection_scope": (
            "routing determinism",
            "responsibility boundaries",
            "paper-only authority",
        ),
        "sanitized_material": "Bounded Stage 3 routing contract summary.",
        "allowed_provider_ids": frozenset({CLAUDE_PROVIDER_ID}),
        "requested_at": NOW,
    }
    values.update(changes)
    return ClaudeInspectionRequest(**values)


def test_independent_inspection_returns_advisory_report() -> None:
    provider = FakeClaudeProvider()
    report = GovernedClaudeInspectionService(provider).inspect(
        request(),
        completed_at=LATER,
    )

    assert report.succeeded
    assert report.provider_id == CLAUDE_PROVIDER_ID
    assert report.findings[0].severity == "medium"
    assert report.findings[0].evidence_references == (DIGEST_B,)
    assert report.report_digest.startswith("sha256:")
    assert report.paper_only is True
    assert report.broker_submission is False
    assert report.execution_authorized is False
    assert report.approval_authority is False
    assert report.portfolio_mutation is False
    assert report.tool_execution is False
    assert len(provider.calls) == 1
    assert provider.calls[0].capability == Capability.REASONING


def test_inspection_requires_explicit_claude_only_admission() -> None:
    with pytest.raises(ClaudeInspectionValidationError, match="Claude-only"):
        request(allowed_provider_ids=frozenset({"local-gemma"}))
    with pytest.raises(ClaudeInspectionValidationError, match="Claude-only"):
        request(
            allowed_provider_ids=frozenset(
                {
                    CLAUDE_PROVIDER_ID,
                    "local-gemma",
                }
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"target_digest": "not-a-digest"},
        {"evidence_digests": ()},
        {"inspection_scope": ()},
        {"sanitized_material": ""},
        {"sanitized_material": "x" * 24_001},
        {"timeout_ms": 0},
        {"paper_only": False},
        {"broker_submission": True},
        {"execution_authorized": True},
        {"approval_authority": True},
        {"portfolio_mutation": True},
        {"tool_execution": True},
    ],
)
def test_inspection_request_fails_closed(changes) -> None:
    with pytest.raises(ClaudeInspectionValidationError):
        request(**changes)


def test_provider_failure_returns_typed_advisory_failure() -> None:
    provider = FakeClaudeProvider(failure=ProviderFailureClass.UNAVAILABLE)
    report = GovernedClaudeInspectionService(provider).inspect(
        request(),
        completed_at=LATER,
    )

    assert report.failure == ClaudeInspectionFailure.PROVIDER_UNAVAILABLE
    assert report.findings == ()
    assert report.paper_only is True
    assert report.broker_submission is False


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"findings": []}),
        json.dumps({"findings": "bad", "limitations": []}),
        json.dumps(
            {
                "findings": [
                    {
                        "finding_id": "finding-001",
                        "severity": "urgent",
                        "category": "routing",
                        "summary": "bad severity",
                        "evidence_references": [DIGEST_B],
                        "recommendation": "none",
                    }
                ],
                "limitations": [],
            }
        ),
        json.dumps(
            {
                "findings": [
                    {
                        "finding_id": "finding-001",
                        "severity": "high",
                        "category": "routing",
                        "summary": "untrusted citation",
                        "evidence_references": ["sha256:" + "c" * 64],
                        "recommendation": "none",
                    }
                ],
                "limitations": [],
            }
        ),
    ],
)
def test_malformed_or_untrusted_output_is_rejected(content: str) -> None:
    report = GovernedClaudeInspectionService(
        FakeClaudeProvider(content=content)
    ).inspect(
        request(),
        completed_at=LATER,
    )

    assert report.failure == ClaudeInspectionFailure.CONTRACT_VIOLATION
    assert report.findings == ()
    assert report.approval_authority is False


def test_inspection_report_is_deterministic() -> None:
    provider = FakeClaudeProvider()
    service = GovernedClaudeInspectionService(provider)

    first = service.inspect(request(), completed_at=LATER)
    second = service.inspect(request(), completed_at=LATER)

    assert first == second
    assert first.report_digest == second.report_digest


def test_non_claude_provider_is_rejected() -> None:
    provider = FakeClaudeProvider()
    provider.identity = ProviderIdentity(
        "local-gemma",
        ExecutionLocation.LOCAL,
    )
    with pytest.raises(ClaudeInspectionValidationError, match="Claude provider"):
        GovernedClaudeInspectionService(provider)
