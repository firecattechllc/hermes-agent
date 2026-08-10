from __future__ import annotations

import pytest
from pydantic import ValidationError

from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.omniroute_evidence import (
    RouteDecisionEvidence,
    RouteStatus,
    build_route_decision_evidence_record,
)


def _decision(**overrides) -> RouteDecisionEvidence:
    fields = dict(
        correlation_id="corr-1",
        requested_capability="lightweight",
        selected_provider="titan_ollama",
        selected_model="hermes-llama3.2:3b-64k",
        is_local_route=True,
        reason="routed_by_priority",
        status=RouteStatus.SUCCEEDED,
        latency_ms=42,
        observed_at=1_000,
    )
    fields.update(overrides)
    return RouteDecisionEvidence(**fields)


def test_succeeded_decision_requires_provider_model_and_locality() -> None:
    with pytest.raises(ValidationError):
        RouteDecisionEvidence(
            correlation_id="c",
            requested_capability="lightweight",
            reason="x",
            status=RouteStatus.SUCCEEDED,
            latency_ms=1,
            observed_at=1,
        )


def test_policy_rejected_status_requires_policy_rejected_flag() -> None:
    with pytest.raises(ValidationError):
        RouteDecisionEvidence(
            correlation_id="c",
            requested_capability="x",
            reason="unknown_alias",
            status=RouteStatus.POLICY_REJECTED,
            policy_rejected=False,
            latency_ms=1,
            observed_at=1,
        )


def test_budget_rejected_status_requires_budget_rejected_flag() -> None:
    with pytest.raises(ValidationError):
        RouteDecisionEvidence(
            correlation_id="c",
            requested_capability="x",
            reason="budget_exceeded",
            status=RouteStatus.BUDGET_REJECTED,
            budget_rejected=False,
            latency_ms=1,
            observed_at=1,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "leaked api_key here"),
        ("provider_error", "authorization: Bearer sk-secret"),
    ],
)
def test_rejects_forbidden_content_in_free_text_fields(field, value) -> None:
    with pytest.raises(ValidationError):
        _decision(
            **{field: value},
            status=RouteStatus.FAILED,
            selected_provider=None,
            selected_model=None,
            is_local_route=None,
        )


def test_redacted_summary_never_contains_secret_markers() -> None:
    decision = _decision()
    summary = decision.redacted_summary()
    assert "api_key" not in summary.lower()
    assert "bearer" not in summary.lower()
    assert "secret" not in summary.lower()
    assert "corr-1" not in summary  # correlation id carried separately, not embedded


def test_all_required_route_fields_are_representable() -> None:
    decision = _decision(
        fallback_attempts=("freellmapi/gpt-4o-mini",),
        timeout_occurred=False,
        provider_error=None,
    )
    dumped = decision.model_dump()
    for field in (
        "requested_capability",
        "selected_provider",
        "selected_model",
        "is_local_route",
        "reason",
        "fallback_attempts",
        "timeout_occurred",
        "provider_error",
        "policy_rejected",
        "budget_rejected",
        "status",
        "latency_ms",
        "correlation_id",
    ):
        assert field in dumped


def test_build_route_decision_evidence_record_appends_to_prime_evidence_store(
    tmp_path,
) -> None:
    store = PrimeEvidenceStore(state_root=tmp_path / "prime")
    decision = _decision()
    record = build_route_decision_evidence_record(
        decision, producer_identity_id="titan-omniroute"
    )
    assert record.kind == "omniroute_route_decision"
    assert record.correlation_id == "corr-1"

    store.append(record)
    entries = store.read_all()
    assert len(entries) == 1
    assert entries[0]["record"]["kind"] == "omniroute_route_decision"
    assert store.verify_chain() is True


def test_evidence_record_redacted_summary_contains_no_correlation_leak_of_secrets(
    tmp_path,
) -> None:
    store = PrimeEvidenceStore(state_root=tmp_path / "prime")
    decision = _decision(
        status=RouteStatus.FAILED, provider_error="upstream unreachable"
    )
    record = build_route_decision_evidence_record(
        decision, producer_identity_id="titan-omniroute"
    )
    store.append(record)
    entries = store.read_all()
    encoded = str(entries[0])
    for marker in (
        "api_key",
        "authorization:",
        "bearer ",
        "password",
        "secret",
        "token=",
    ):
        assert marker not in encoded.lower()
