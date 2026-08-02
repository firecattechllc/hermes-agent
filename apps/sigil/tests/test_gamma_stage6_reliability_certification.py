from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigil.ai import (
    CLAUDE_PROVIDER_ID,
    Capability,
    ClaudeInspectionFailure,
    ClaudeInspectionRequest,
    CrossProviderValidationState,
    DurableClaudeInspectionStore,
    DurableCrossProviderValidationStore,
    ExecutionLocation,
    GovernedClaudeInspectionService,
    HermesClaudeTransport,
    ProviderClaim,
    ProviderFailure,
    ProviderFailureClass,
    ProviderIdentity,
    ProviderResult,
    build_invocation_evidence,
    certify_cross_provider_validation,
    claude_inspection_status,
    cross_provider_validation_status,
    validate_cross_provider_claims,
)
from sigil.ai.hermes_claude_transport import (
    ClaudeTransportError,
    ClaudeTransportFailure,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-02T23:00:00Z"
LATER = "2026-08-02T23:00:01Z"


class ReliabilityClaudeProvider:
    model_id = "claude-sonnet-governed"
    model_version = "gamma-reliability"
    capabilities = frozenset({Capability.REASONING})
    request_timeout_ms = 60_000

    def __init__(
        self,
        *,
        content: object = None,
        failure: ProviderFailureClass | None = None,
    ) -> None:
        self.identity = ProviderIdentity(
            CLAUDE_PROVIDER_ID,
            ExecutionLocation.EXTERNAL,
        )
        self.content = (
            json.dumps(
                {
                    "findings": [],
                    "limitations": ["Bounded reliability inspection."],
                }
            )
            if content is None
            else content
        )
        self.failure = failure
        self.calls = 0

    def invoke(self, invocation):
        self.calls += 1
        failure = (
            None
            if self.failure is None
            else ProviderFailure(
                self.failure,
                "provider failed safely",
                False,
            )
        )
        output = (
            None
            if failure is not None
            else {"content": self.content}
        )
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
            failure_classification=(
                None
                if failure is None
                else failure.classification.value
            ),
            input_payload={"prompt_digest": DIGEST_A},
            output_payload=(
                None
                if output is None
                else {"content_digest": DIGEST_B}
            ),
            provider_metadata=(("adapter", "stage6-reliability"),),
        )
        return ProviderResult(
            output=output,
            failure=failure,
            evidence=evidence,
        )


def inspection_request() -> ClaudeInspectionRequest:
    return ClaudeInspectionRequest(
        inspection_id="stage6-inspection",
        task_correlation_id="stage6-task",
        target_revision="c5c8f293e",
        target_digest=DIGEST_A,
        evidence_digests=(DIGEST_B,),
        inspection_scope=("reliability",),
        sanitized_material="Bounded reliability certification material.",
        allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
        requested_at=NOW,
    )


def provider_claim(
    claim_id: str,
    provider_id: str,
    value: str,
    *,
    evidence: tuple[str, ...] = (DIGEST_B,),
) -> ProviderClaim:
    return ProviderClaim(
        claim_id=claim_id,
        provider_id=provider_id,
        model_id=(
            "gemma-governed"
            if provider_id == "local-gemma"
            else "claude-sonnet-governed"
        ),
        subject="routing",
        normalized_value=value,
        evidence_references=evidence,
    )


def validation_report(
    *,
    gemma_value: str = "safe",
    claude_value: str = "safe",
    claude_evidence: tuple[str, ...] = (DIGEST_B,),
):
    return validate_cross_provider_claims(
        target_revision="c5c8f293e",
        target_digest=DIGEST_A,
        gemma_claims=(
            provider_claim("g-1", "local-gemma", gemma_value),
        ),
        claude_claims=(
            provider_claim(
                "c-1",
                "hermes-claude",
                claude_value,
                evidence=claude_evidence,
            ),
        ),
        validated_at=NOW,
    )


@pytest.mark.parametrize(
    ("kwargs", "classification"),
    [
        (
            {
                "model": "claude",
                "prompt": "inspect",
                "timeout_ms": 0,
                "max_output_tokens": 100,
            },
            ClaudeTransportFailure.TIMEOUT,
        ),
        (
            {
                "model": "",
                "prompt": "inspect",
                "timeout_ms": 1_000,
                "max_output_tokens": 100,
            },
            ClaudeTransportFailure.MALFORMED,
        ),
        (
            {
                "model": "claude",
                "prompt": "",
                "timeout_ms": 1_000,
                "max_output_tokens": 100,
            },
            ClaudeTransportFailure.MALFORMED,
        ),
        (
            {
                "model": "claude",
                "prompt": "inspect",
                "timeout_ms": 1_000,
                "max_output_tokens": 0,
            },
            ClaudeTransportFailure.MALFORMED,
        ),
    ],
)
def test_transport_input_failures_are_typed_and_fail_closed(
    kwargs,
    classification,
) -> None:
    transport = HermesClaudeTransport(
        credential_resolver=lambda: "unused",
    )

    with pytest.raises(ClaudeTransportError) as caught:
        transport.invoke(**kwargs)

    assert caught.value.classification == classification
    assert "unused" not in str(caught.value)


def test_missing_credentials_are_unavailable_without_secret_leakage() -> None:
    transport = HermesClaudeTransport(
        credential_resolver=lambda: None,
    )

    with pytest.raises(ClaudeTransportError) as caught:
        transport.invoke(
            model="claude",
            prompt="inspect",
            timeout_ms=1_000,
            max_output_tokens=100,
        )

    assert (
        caught.value.classification
        == ClaudeTransportFailure.UNAVAILABLE
    )
    assert "credential" in str(caught.value).lower()
    assert "secret" not in str(caught.value).lower()


@pytest.mark.parametrize(
    ("content", "failure"),
    [
        ("not-json", ClaudeInspectionFailure.CONTRACT_VIOLATION),
        (
            json.dumps({"findings": []}),
            ClaudeInspectionFailure.CONTRACT_VIOLATION,
        ),
        (
            {"unexpected": "object"},
            ClaudeInspectionFailure.MALFORMED_OUTPUT,
        ),
    ],
)
def test_inspection_malformed_outputs_fail_closed(
    content,
    failure,
) -> None:
    report = GovernedClaudeInspectionService(
        ReliabilityClaudeProvider(content=content)
    ).inspect(
        inspection_request(),
        completed_at=LATER,
    )

    assert report.failure == failure
    assert report.findings == ()
    assert report.approval_authority is False
    assert report.execution_authorized is False
    assert report.broker_submission is False


def test_provider_unavailable_produces_typed_inspection_failure() -> None:
    report = GovernedClaudeInspectionService(
        ReliabilityClaudeProvider(
            failure=ProviderFailureClass.UNAVAILABLE
        )
    ).inspect(
        inspection_request(),
        completed_at=LATER,
    )

    assert report.failure == ClaudeInspectionFailure.PROVIDER_UNAVAILABLE
    assert report.findings == ()
    assert report.paper_only is True


def test_inspection_replay_is_deterministic() -> None:
    service = GovernedClaudeInspectionService(
        ReliabilityClaudeProvider()
    )

    first = service.inspect(
        inspection_request(),
        completed_at=LATER,
    )
    second = service.inspect(
        inspection_request(),
        completed_at=LATER,
    )

    assert first == second
    assert first.report_digest == second.report_digest


def test_validation_disagreement_and_partial_evidence_require_review() -> None:
    disagreement = validation_report(
        gemma_value="low",
        claude_value="high",
    )
    partial = validation_report(
        claude_evidence=("sha256:" + "c" * 64,),
    )

    assert disagreement.state == CrossProviderValidationState.REVIEW_REQUIRED
    assert disagreement.human_review_required is True
    assert (
        partial.state
        == CrossProviderValidationState.INSUFFICIENT_EVIDENCE
    )
    assert partial.human_review_required is True

    disagreement_certification = certify_cross_provider_validation(
        disagreement,
        certified_at=NOW,
    )
    partial_certification = certify_cross_provider_validation(
        partial,
        certified_at=NOW,
    )

    assert disagreement_certification.promotion_authorized is False
    assert partial_certification.promotion_authorized is False
    assert disagreement_certification.release_authority is False
    assert partial_certification.release_authority is False


def test_validation_replay_is_order_independent_and_deterministic() -> None:
    first = validation_report()
    second = validation_report()

    assert first == second
    assert first.validation_id == second.validation_id
    assert first.validation_digest == second.validation_digest


def test_corrupt_inspection_store_status_fails_closed(
    tmp_path: Path,
) -> None:
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    store.path.write_text("corrupt\n")

    status = claude_inspection_status(tmp_path.resolve())

    assert status["state"] == "invalid"
    assert status["store_health"] == "corrupt"
    assert status["report_count"] == 0
    assert status["approval_authority"] is False
    assert status["execution_authorized"] is False


def test_corrupt_validation_store_status_fails_closed(
    tmp_path: Path,
) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    store.path.write_text("corrupt\n")

    status = cross_provider_validation_status(tmp_path.resolve())

    assert status["state"] == "invalid"
    assert status["store_health"] == "corrupt"
    assert status["report_count"] == 0
    assert status["promotion_authorized"] is False
    assert status["release_authority"] is False


def test_truncated_inspection_store_recovers_only_when_explicit(
    tmp_path: Path,
) -> None:
    service = GovernedClaudeInspectionService(
        ReliabilityClaudeProvider()
    )
    report = service.inspect(
        inspection_request(),
        completed_at=LATER,
    )
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    store.append(report)

    with store.path.open("ab") as output:
        output.write(b'{"partial":')

    with pytest.raises(Exception):
        store.read_reports(recover_truncated_tail=False)

    assert store.read_reports(recover_truncated_tail=True) == (report,)


def test_validation_store_replay_preserves_typed_state(
    tmp_path: Path,
) -> None:
    report = validation_report()
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    store.append(report)

    replayed = store.read_reports()[0]

    assert replayed == report
    assert replayed.state == CrossProviderValidationState.CONSISTENT
    assert replayed.validation_digest == report.validation_digest


def test_all_stage6_outputs_remain_advisory_only() -> None:
    inspection = GovernedClaudeInspectionService(
        ReliabilityClaudeProvider()
    ).inspect(
        inspection_request(),
        completed_at=LATER,
    )
    validation = validation_report()
    certification = certify_cross_provider_validation(
        validation,
        certified_at=NOW,
    )

    assert inspection.approval_authority is False
    assert inspection.execution_authorized is False
    assert inspection.broker_submission is False
    assert inspection.portfolio_mutation is False
    assert inspection.tool_execution is False

    assert validation.promotion_authorized is False
    assert validation.release_authority is False
    assert validation.approval_authority is False
    assert validation.execution_authorized is False
    assert validation.broker_submission is False
    assert validation.portfolio_mutation is False
    assert validation.tool_execution is False

    assert certification.promotion_authorized is False
    assert certification.release_authority is False
    assert certification.approval_authority is False
    assert certification.execution_authorized is False
    assert certification.broker_submission is False
    assert certification.portfolio_mutation is False
    assert certification.tool_execution is False
