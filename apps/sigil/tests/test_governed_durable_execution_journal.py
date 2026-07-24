from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket

import pytest

from sigil.execution import (
    DurableExecutionJournal,
    ExecutionJournalConflictError,
    ExecutionJournalCorruptionError,
    ExecutionJournalError,
    ExecutionJournalEventType,
    ExecutionRecoveryClassification,
)
from sigil.integrations.providers import (
    GovernedEquityTradeProposal,
    GovernedTradeApproval,
    PublicCancellationApproval,
    PublicExecutionPolicy,
    PublicExecutionState,
)
from sigil.integrations.providers.public_execution import (
    PublicOrderExecution,
    PublicPortfolioSnapshot,
    PublicPreflightRecord,
    PublicSubmissionIntent,
    _digest,
    _public_body,
)


NOW = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "acct_durable_test"
ORDER_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in durable journal tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def policy() -> PublicExecutionPolicy:
    return PublicExecutionPolicy(
        maximum_notional_per_order="1000",
        maximum_whole_share_quantity="100",
        maximum_fractional_notional="500",
        allowed_symbols=("AAPL",),
        proposal_lifetime_seconds=600,
        approval_lifetime_seconds=300,
        preflight_lifetime_seconds=120,
        portfolio_freshness_seconds=30,
    )


def artifacts() -> tuple[
    GovernedEquityTradeProposal,
    PublicPortfolioSnapshot,
    PublicPreflightRecord,
    GovernedTradeApproval,
    PublicSubmissionIntent,
]:
    execution_policy = policy()
    proposal = GovernedEquityTradeProposal.create(
        policy=execution_policy,
        account_id=ACCOUNT_ID,
        symbol="AAPL",
        side="BUY",
        order_type="LIMIT",
        quantity="2",
        limit_price="100",
        purpose="operator directed offline journal test",
        created_at=NOW,
        correlation_id="corr-durable-test",
        requested_by="operator-test",
    )
    portfolio_payload = {
        "accountId": ACCOUNT_ID,
        "buyingPower": {"cashOnlyBuyingPower": "500"},
        "positions": [],
    }
    snapshot = PublicPortfolioSnapshot.from_payload(ACCOUNT_ID, portfolio_payload, NOW)
    preflight_payload = {
        "estimatedCost": "200",
        "buyingPowerRequirement": "200",
        "regulatoryFees": {"secFee": "0"},
    }
    preflight = PublicPreflightRecord.create(
        proposal, preflight_payload, NOW, execution_policy
    )
    approval = GovernedTradeApproval.create(
        proposal=proposal,
        preflight=preflight,
        maximum_authorized_notional="300",
        approved_at=NOW,
        approved_by="human-operator",
        single_use_nonce="durable-approval-once",
        policy=execution_policy,
    )
    body_hash = _digest(_public_body(proposal, order_id=ORDER_ID))
    intent_hash = _digest(
        {
            "order_id": ORDER_ID,
            "proposal": proposal.proposal_id,
            "preflight": preflight.preflight_id,
            "approval": approval.approval_id,
            "body_hash": body_hash,
        }
    )
    intent = PublicSubmissionIntent(
        intent_id=f"public-intent-{intent_hash}",
        order_id=ORDER_ID,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        preflight_id=preflight.preflight_id,
        preflight_hash=preflight.preflight_hash,
        approval_id=approval.approval_id,
        approval_hash=approval.approval_hash,
        account_binding=proposal.account_binding,
        body_hash=body_hash,
        recorded_at=NOW,
        correlation_id=proposal.correlation_id,
    )
    return proposal, snapshot, preflight, approval, intent


def repository(tmp_path: Path, **kwargs: object) -> DurableExecutionJournal:
    root = tmp_path / "journal"
    root.mkdir(parents=True)
    return DurableExecutionJournal(root, **kwargs)  # type: ignore[arg-type]


def record_intent(
    journal: DurableExecutionJournal,
) -> tuple[GovernedEquityTradeProposal, PublicSubmissionIntent, PublicOrderExecution]:
    proposal, snapshot, preflight, approval, intent = artifacts()
    journal.record_proposal(proposal)
    journal.record_portfolio_snapshot(proposal, snapshot)
    journal.record_preflight(proposal, preflight)
    journal.record_intent(intent, approval)
    execution = PublicOrderExecution(
        order_id=ORDER_ID,
        intent_id=intent.intent_id,
        account_binding=proposal.account_binding,
        state=PublicExecutionState.SUBMISSION_INTENT_RECORDED,
        updated_at=NOW,
    )
    journal.save_execution(execution)
    return proposal, intent, execution


def records(journal: DurableExecutionJournal, execution_id: str) -> list[Path]:
    return sorted((journal.root / "executions" / execution_id).glob("*.json"))


def test_empty_repository_initialization_and_read_only_audit(tmp_path: Path) -> None:
    journal = repository(tmp_path)

    assert journal.audit() == ()
    assert journal.execution_ids() == ()
    assert sorted(item.name for item in journal.root.iterdir()) == [
        ".journal.lock",
        "executions",
    ]


def test_valid_first_event_and_exact_idempotent_append(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal = artifacts()[0]

    journal.record_proposal(proposal)
    before = records(journal, proposal.proposal_id)
    journal.record_proposal(proposal)

    assert records(journal, proposal.proposal_id) == before
    value = json.loads(before[0].read_text())
    assert value["sequence"] == 1
    assert value["previous_entry_hash"] == "0" * 64
    assert value["event_type"] == "proposal_created"


def test_full_successful_submission_and_terminal_chain(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, intent, execution = record_intent(journal)
    submitted = execution.transition(
        PublicExecutionState.SUBMITTED, at=NOW, response_hash="1" * 64
    )
    journal.save_execution(submitted)
    journal.record_reconciliation_attempt(submitted, NOW + timedelta(seconds=1))
    filled = submitted.transition(
        PublicExecutionState.FILLED,
        at=NOW + timedelta(seconds=2),
        response_hash="2" * 64,
    )
    journal.save_execution(filled)

    recovered = journal.execution(ORDER_ID)
    inspection = journal.inspect(proposal.proposal_id)
    assert recovered.state is PublicExecutionState.FILLED
    assert recovered.intent_id == intent.intent_id
    assert inspection.classification is ExecutionRecoveryClassification.COMPLETE
    assert inspection.provider_order_id == ORDER_ID


def test_explicit_rejection_and_ambiguous_submission_classification(
    tmp_path: Path,
) -> None:
    rejected_journal = repository(tmp_path / "rejected")
    proposal, _, execution = record_intent(rejected_journal)
    submitted = execution.transition(PublicExecutionState.SUBMITTED, at=NOW)
    rejected_journal.save_execution(submitted)
    rejected_journal.save_execution(
        submitted.transition(PublicExecutionState.REJECTED, at=NOW)
    )
    assert (
        rejected_journal.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.REJECTED
    )

    ambiguous_journal = repository(tmp_path / "ambiguous")
    proposal, _, execution = record_intent(ambiguous_journal)
    ambiguous_journal.save_execution(
        execution.transition(
            PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=NOW
        )
    )
    assert (
        ambiguous_journal.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.RECONCILIATION_REQUIRED
    )
    assert ambiguous_journal.execution(ORDER_ID).order_id == ORDER_ID


def test_known_pretransmission_failure_is_safely_retryable(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, intent, execution = record_intent(journal)
    journal.record_submission_not_started(
        execution, reason="FinancialDataAuthenticationError", at=NOW
    )

    assert journal.submission_retry_permitted(intent.intent_id) is True
    assert (
        journal.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.SAFELY_RETRYABLE_BEFORE_SUBMISSION
    )


def test_reconciliation_to_existing_and_no_order(tmp_path: Path) -> None:
    existing = repository(tmp_path / "existing")
    proposal, _, execution = record_intent(existing)
    ambiguous = execution.transition(
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=NOW
    )
    existing.save_execution(ambiguous)
    existing.record_reconciliation_attempt(ambiguous, NOW + timedelta(seconds=1))
    acknowledged = ambiguous.transition(
        PublicExecutionState.ACKNOWLEDGED,
        at=NOW + timedelta(seconds=2),
        response_hash="3" * 64,
    )
    existing.save_execution(acknowledged)
    assert existing.execution(ORDER_ID).state is PublicExecutionState.ACKNOWLEDGED

    missing = repository(tmp_path / "missing")
    proposal, _, execution = record_intent(missing)
    ambiguous = execution.transition(
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=NOW
    )
    missing.save_execution(ambiguous)
    missing.record_reconciliation_attempt(ambiguous, NOW + timedelta(seconds=1))
    missing.save_execution(ambiguous)
    assert (
        missing.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.RECONCILIATION_REQUIRED
    )
    assert json.loads(records(missing, proposal.proposal_id)[-1].read_text())["payload"][
        "broker_order_exists"
    ] is False


def test_governed_and_ambiguous_cancellation_chain(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, _, execution = record_intent(journal)
    submitted = execution.transition(PublicExecutionState.SUBMITTED, at=NOW)
    journal.save_execution(submitted)
    approval = PublicCancellationApproval.create(
        order_id=ORDER_ID,
        account_id=ACCOUNT_ID,
        approved_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=2),
        approved_by="human-operator",
        single_use_nonce="cancel-once",
    )
    journal.consume_cancellation(approval)
    journal.record_cancellation_intent(submitted, approval, NOW + timedelta(seconds=1))
    acknowledged = submitted.transition(
        PublicExecutionState.CANCELLATION_REQUESTED,
        at=NOW + timedelta(seconds=2),
    )
    journal.save_execution(acknowledged)
    assert journal.execution(ORDER_ID).state is PublicExecutionState.CANCELLATION_REQUESTED

    second_journal = repository(tmp_path / "ambiguous")
    proposal, _, submitted = record_intent(second_journal)
    submitted = submitted.transition(PublicExecutionState.SUBMITTED, at=NOW)
    second_journal.save_execution(submitted)
    second_journal.consume_cancellation(approval)
    second_journal.record_cancellation_intent(
        submitted, approval, NOW + timedelta(seconds=1)
    )
    ambiguous = submitted.transition(
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
        at=NOW + timedelta(seconds=2),
    )
    second_journal.save_execution(ambiguous)

    inspection = second_journal.inspect(proposal.proposal_id)
    assert (
        inspection.classification
        is ExecutionRecoveryClassification.CANCELLATION_RECONCILIATION_REQUIRED
    )
    event_types = [
        json.loads(path.read_text())["event_type"]
        for path in records(second_journal, proposal.proposal_id)
    ]
    assert event_types[-3:] == [
        "cancellation_approval_consumed",
        "cancellation_intent_recorded",
        "cancellation_outcome_ambiguous",
    ]
    second_journal.record_reconciliation_attempt(
        ambiguous, NOW + timedelta(seconds=3)
    )
    cancelled = ambiguous.transition(
        PublicExecutionState.CANCELLED, at=NOW + timedelta(seconds=4)
    )
    second_journal.record_reconciliation_result(
        cancelled, broker_order_exists=True, at=NOW + timedelta(seconds=4)
    )
    second_journal.save_execution(cancelled)
    completed_types = [
        json.loads(path.read_text())["event_type"]
        for path in records(second_journal, proposal.proposal_id)
    ]
    assert "cancellation_reconciliation_completed" in completed_types
    assert (
        second_journal.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.COMPLETE
    )


def test_conflicting_duplicate_and_changed_terms_fail(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal = artifacts()[0]
    journal.record_proposal(proposal)

    with pytest.raises(ExecutionJournalConflictError):
        journal._append(  # type: ignore[attr-defined]
            proposal.proposal_id,
            ExecutionJournalEventType.PROPOSAL_CREATED,
            {"order_terms": {"symbol": "SPY"}, "proposal_id": proposal.proposal_id},
            NOW,
        )


def test_concurrent_writer_is_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal = artifacts()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: journal.record_proposal(proposal), range(2)))

    assert results == (None, None)
    assert len(records(journal, proposal.proposal_id)) == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("sequence", "hash"),
        ("previous", "hash"),
        ("payload", "hash"),
        ("filename", "filename"),
        ("version", "version"),
        ("execution", "hash"),
        ("truncate", "truncated"),
    ],
)
def test_corruption_detection(
    tmp_path: Path, mutation: str, message: str
) -> None:
    journal = repository(tmp_path)
    proposal = artifacts()[0]
    journal.record_proposal(proposal)
    path = records(journal, proposal.proposal_id)[0]
    value = json.loads(path.read_text())
    if mutation == "sequence":
        value["sequence"] = 2
    elif mutation == "previous":
        value["previous_entry_hash"] = "1" * 64
    elif mutation == "payload":
        value["payload"]["proposal_id"] = "tampered"
    elif mutation == "version":
        value["journal_version"] = 99
    elif mutation == "execution":
        value["execution_id"] = "other-execution"
    elif mutation == "truncate":
        path.write_bytes(path.read_bytes()[:10])
    else:
        path.rename(path.with_name("00000001-" + "f" * 64 + ".json"))
    if mutation not in {"filename", "truncate"}:
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ExecutionJournalCorruptionError, match=message):
        journal._load(proposal.proposal_id)  # type: ignore[attr-defined]
    assert (
        journal.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.CORRUPT
    )


def test_sequence_gap_duplicate_and_unexpected_files_fail(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, snapshot, _, _, _ = artifacts()
    journal.record_proposal(proposal)
    journal.record_portfolio_snapshot(proposal, snapshot)
    paths = records(journal, proposal.proposal_id)
    paths[0].unlink()
    with pytest.raises(ExecutionJournalCorruptionError, match="sequence"):
        journal._load(proposal.proposal_id)  # type: ignore[attr-defined]

    root = tmp_path / "unexpected"
    root.mkdir()
    (root / "unmanaged.txt").write_text("unexpected")
    with pytest.raises(ExecutionJournalCorruptionError, match="unexpected"):
        DurableExecutionJournal(root)


def test_invalid_transition_reused_approval_and_cross_account_fail(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, _, _, approval, intent = artifacts()
    with pytest.raises(ExecutionJournalCorruptionError, match="transition"):
        journal.record_intent(intent, approval)

    journal = repository(tmp_path / "cross")
    record_intent(journal)
    other = PublicCancellationApproval.create(
        order_id=ORDER_ID,
        account_id="other_account",
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        approved_by="human",
        single_use_nonce="other",
    )
    with pytest.raises(ExecutionJournalCorruptionError, match="terms changed"):
        journal.consume_cancellation(other)


@pytest.mark.parametrize("root_kind", ["relative", "missing", "file", "symlink"])
def test_repository_path_and_symlink_defenses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_kind: str
) -> None:
    if root_kind == "relative":
        monkeypatch.chdir(tmp_path)
        root = Path("relative")
        root.mkdir()
    elif root_kind == "missing":
        root = tmp_path / "missing"
    elif root_kind == "file":
        root = tmp_path / "file"
        root.write_text("file")
    else:
        target = tmp_path / "target"
        target.mkdir()
        root = tmp_path / "link"
        root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ExecutionJournalError):
        DurableExecutionJournal(root)


def test_path_traversal_and_secret_material_rejected(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    with pytest.raises(ExecutionJournalError, match="safe"):
        journal.inspect("../escape")
    with pytest.raises(ExecutionJournalError, match="secret"):
        journal._append(  # type: ignore[attr-defined]
            "safe-execution",
            ExecutionJournalEventType.PROPOSAL_CREATED,
            {"authorization": "Bearer fictional"},
            NOW,
        )


def test_record_and_repository_bounds(tmp_path: Path) -> None:
    with pytest.raises(ExecutionJournalError, match="record byte"):
        repository(tmp_path / "small", max_record_bytes=100)
    journal = repository(tmp_path / "capacity", max_execution_records=1)
    proposal = artifacts()[0]
    journal.record_proposal(proposal)
    with pytest.raises(ExecutionJournalError, match="capacity"):
        journal.record_portfolio_snapshot(proposal, artifacts()[1])


def test_atomic_write_and_directory_durability_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = repository(tmp_path)
    proposal = artifacts()[0]
    monkeypatch.setattr(os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fail")))
    with pytest.raises(ExecutionJournalError, match="commit failed"):
        journal.record_proposal(proposal)
    assert records(journal, proposal.proposal_id) == []

    journal = repository(tmp_path / "directory")
    original = journal._fsync_directory  # type: ignore[attr-defined]
    monkeypatch.setattr(journal, "_fsync_directory", lambda path: (_ for _ in ()).throw(OSError("fsync")))
    with pytest.raises(ExecutionJournalError, match="commit failed"):
        journal.record_proposal(proposal)
    monkeypatch.setattr(journal, "_fsync_directory", original)


def test_persisted_transport_diagnostics_are_bounded_and_secret_free(
    tmp_path: Path,
) -> None:
    journal = repository(tmp_path)
    proposal = artifacts()[0]
    event = journal._append(  # type: ignore[attr-defined]
        proposal.proposal_id,
        ExecutionJournalEventType.PROPOSAL_CREATED,
        {
            "diagnostic_code": "transport_timeout",
            "order_terms": {
                "account_binding": proposal.account_binding,
                "symbol": proposal.symbol,
            },
        },
        NOW,
    )
    persisted = records(journal, proposal.proposal_id)[0].read_text()
    assert event.payload["diagnostic_code"] == "transport_timeout"
    assert "Bearer" not in persisted
    assert ACCOUNT_ID not in persisted


def test_recovery_is_read_only_and_performs_no_broker_mutation(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, _, execution = record_intent(journal)
    journal.save_execution(
        execution.transition(
            PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=NOW
        )
    )
    before = tuple(path.read_bytes() for path in records(journal, proposal.proposal_id))

    first = journal.audit()
    second = journal.inspect(proposal.proposal_id)

    assert first[0] == second
    assert tuple(path.read_bytes() for path in records(journal, proposal.proposal_id)) == before


def test_quarantine_is_immutable_and_fail_closed(tmp_path: Path) -> None:
    journal = repository(tmp_path)
    proposal, _, execution = record_intent(journal)
    journal.quarantine(execution, reason_code="operator_integrity_hold", at=NOW)

    assert (
        journal.inspect(proposal.proposal_id).classification
        is ExecutionRecoveryClassification.QUARANTINED
    )
    with pytest.raises(ExecutionJournalCorruptionError, match="transition"):
        journal.save_execution(
            execution.transition(PublicExecutionState.SUBMITTED, at=NOW)
        )
