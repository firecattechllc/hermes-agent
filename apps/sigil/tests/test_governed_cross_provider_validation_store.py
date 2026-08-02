from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigil.ai import (
    CrossProviderValidationState,
    CrossProviderValidationStoreConflictError,
    CrossProviderValidationStoreCorruptionError,
    CrossProviderValidationStoreError,
    DurableCrossProviderValidationStore,
    ProviderClaim,
    cross_provider_validation_status,
    validate_cross_provider_claims,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-02T22:00:00Z"


def claim(
    claim_id: str,
    provider_id: str,
    subject: str,
    value: str,
) -> ProviderClaim:
    return ProviderClaim(
        claim_id=claim_id,
        provider_id=provider_id,
        model_id=(
            "gemma-governed"
            if provider_id == "local-gemma"
            else "claude-sonnet-governed"
        ),
        subject=subject,
        normalized_value=value,
        evidence_references=(DIGEST_B,),
    )


def report(
    subject: str = "routing",
    *,
    gemma_value: str = "safe",
    claude_value: str = "safe",
):
    return validate_cross_provider_claims(
        target_revision="360d4730d",
        target_digest=DIGEST_A,
        gemma_claims=(
            claim("gemma-claim", "local-gemma", subject, gemma_value),
        ),
        claude_claims=(
            claim("claude-claim", "hermes-claude", subject, claude_value),
        ),
        validated_at=NOW,
    )


def test_store_round_trip_and_hash_chain(tmp_path: Path) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    first = report()
    second = report(
        "risk",
        gemma_value="low",
        claude_value="high",
    )

    store.append(first)
    store.append(second)

    assert store.read_reports() == (first, second)

    lines = store.path.read_text().splitlines()
    first_envelope = json.loads(lines[0])
    second_envelope = json.loads(lines[1])

    assert first_envelope["sequence"] == 1
    assert second_envelope["sequence"] == 2
    assert second_envelope["previous_entry_hash"] == first_envelope["entry_hash"]


def test_duplicate_validation_identity_is_rejected(tmp_path: Path) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    validation = report()

    store.append(validation)

    with pytest.raises(CrossProviderValidationStoreConflictError):
        store.append(validation)


def test_hash_tampering_fails_closed(tmp_path: Path) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    store.append(report())

    envelope = json.loads(store.path.read_text())
    envelope["report"]["state"] = "review_required"
    store.path.write_text(json.dumps(envelope) + "\n")

    with pytest.raises(CrossProviderValidationStoreCorruptionError):
        store.read_reports()


def test_truncated_tail_recovery_is_explicit(tmp_path: Path) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    first = report()
    store.append(first)

    with store.path.open("ab") as output:
        output.write(b'{"truncated":')

    with pytest.raises(
        CrossProviderValidationStoreCorruptionError,
        match="truncated tail",
    ):
        store.read_reports(recover_truncated_tail=False)

    assert store.read_reports(recover_truncated_tail=True) == (first,)


def test_store_rejects_relative_and_unsafe_roots(tmp_path: Path) -> None:
    with pytest.raises(CrossProviderValidationStoreError):
        DurableCrossProviderValidationStore(Path("relative"))

    target = tmp_path / "target"
    target.mkdir()
    unsafe = tmp_path / "unsafe"
    unsafe.symlink_to(target, target_is_directory=True)

    with pytest.raises(CrossProviderValidationStoreError):
        DurableCrossProviderValidationStore(unsafe)


def test_status_projection_is_sanitized_and_advisory_only(
    tmp_path: Path,
) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    store.append(report())
    store.append(
        report(
            "risk",
            gemma_value="low",
            claude_value="high",
        )
    )

    status = cross_provider_validation_status(tmp_path.resolve())

    assert status["state"] == "ready"
    assert status["store_health"] == "healthy"
    assert status["report_count"] == 2
    assert status["consistent_count"] == 1
    assert status["review_required_count"] == 1
    assert status["latest_report"]["state"] == "review_required"
    assert status["latest_report"]["human_review_required"] is True
    assert status["promotion_authorized"] is False
    assert status["release_authority"] is False
    assert status["approval_authority"] is False
    assert status["execution_authorized"] is False
    assert status["broker_submission"] is False
    assert status["portfolio_mutation"] is False
    assert status["tool_execution"] is False
    assert status["paper_only"] is True

    serialized = json.dumps(status)
    assert "gemma_value" not in serialized
    assert "claude_value" not in serialized
    assert "shared_evidence" not in serialized


def test_empty_and_corrupt_status_fail_closed(tmp_path: Path) -> None:
    empty = cross_provider_validation_status(tmp_path.resolve())
    assert empty["state"] == "empty"
    assert empty["report_count"] == 0

    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    store.path.write_text("corrupt\n")

    invalid = cross_provider_validation_status(tmp_path.resolve())
    assert invalid["state"] == "invalid"
    assert invalid["store_health"] == "corrupt"
    assert invalid["report_count"] == 0
    assert invalid["promotion_authorized"] is False


def test_decoded_state_remains_typed(tmp_path: Path) -> None:
    store = DurableCrossProviderValidationStore(tmp_path.resolve())
    store.append(report())

    decoded = store.read_reports()[0]

    assert decoded.state == CrossProviderValidationState.CONSISTENT
