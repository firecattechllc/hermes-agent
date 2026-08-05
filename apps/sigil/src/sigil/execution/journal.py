"""Crash-safe, append-only journal for governed Public order execution.

The repository is deliberately local and caller-supplied.  It is an execution
ledger, not a generic event store: callers can only reach the closed methods
used by the governed Public execution service.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator, Mapping

from sigil.integrations.providers.models import FinancialDataValidationError
from sigil.integrations.providers.public_execution import (
    PUBLIC_EXECUTION_PROVIDER_ID,
    GovernedEquityTradeProposal,
    GovernedTradeApproval,
    PublicAuditEvidence,
    PublicCancellationApproval,
    PublicExecutionState,
    PublicOrderExecution,
    PublicPortfolioSnapshot,
    PublicPreflightRecord,
    PublicSubmissionIntent,
)


JOURNAL_VERSION = 1
MAX_RECORD_BYTES = 64_000
MAX_EXECUTION_RECORDS = 1_000
MAX_REPOSITORY_RECORDS = 100_000
_ZERO_HASH = "0" * 64
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$", re.ASCII)
_HASH = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECORD_NAME = re.compile(r"^([0-9]{8})-([0-9a-f]{64})\.json$", re.ASCII)
_RECORD_KEYS = frozenset(
    {
        "created_at",
        "entry_hash",
        "event_type",
        "execution_id",
        "journal_version",
        "payload",
        "previous_entry_hash",
        "sequence",
    }
)
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "private_key",
    "secret",
    "set-cookie",
    "token",
}


class ExecutionJournalError(RuntimeError):
    """Base failure for the governed durable journal."""


class ExecutionJournalCorruptionError(ExecutionJournalError):
    """Persisted journal content is malformed, unexpected, or inconsistent."""


class ExecutionJournalConflictError(ExecutionJournalError):
    """A concurrent or duplicate write conflicts with committed history."""


class ExecutionJournalEventType(StrEnum):
    PROPOSAL_CREATED = "proposal_created"
    PORTFOLIO_SNAPSHOT_BOUND = "portfolio_snapshot_bound"
    PREFLIGHT_RESULT_BOUND = "preflight_result_bound"
    APPROVAL_CONSUMED = "approval_consumed"
    SUBMISSION_INTENT_RECORDED = "submission_intent_recorded"
    SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION = (
        "submission_transport_failed_before_transmission"
    )
    BROKER_SUBMISSION_ACKNOWLEDGED = "broker_submission_acknowledged"
    SUBMISSION_OUTCOME_AMBIGUOUS = "submission_outcome_ambiguous"
    BROKER_ORDER_ID_ASSOCIATED = "broker_order_id_associated"
    RECONCILIATION_ATTEMPTED = "reconciliation_attempted"
    RECONCILIATION_RESULT_RECORDED = "reconciliation_result_recorded"
    TERMINAL_ORDER_STATUS_OBSERVED = "terminal_order_status_observed"
    CANCELLATION_PROPOSAL_CREATED = "cancellation_proposal_created"
    CANCELLATION_APPROVAL_CONSUMED = "cancellation_approval_consumed"
    CANCELLATION_INTENT_RECORDED = "cancellation_intent_recorded"
    CANCELLATION_ACKNOWLEDGED = "cancellation_acknowledged"
    CANCELLATION_OUTCOME_AMBIGUOUS = "cancellation_outcome_ambiguous"
    CANCELLATION_RECONCILIATION_COMPLETED = "cancellation_reconciliation_completed"
    PERMANENTLY_REJECTED = "permanently_rejected"
    QUARANTINED = "quarantined"


class ExecutionRecoveryClassification(StrEnum):
    COMPLETE = "complete"
    REJECTED = "rejected"
    SAFELY_RETRYABLE_BEFORE_SUBMISSION = "safely_retryable_before_submission"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    CANCELLATION_RECONCILIATION_REQUIRED = "cancellation_reconciliation_required"
    QUARANTINED = "quarantined"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ExecutionJournalEvent:
    journal_version: int
    execution_id: str
    sequence: int
    event_type: ExecutionJournalEventType
    payload: Mapping[str, object]
    previous_entry_hash: str
    entry_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionRecoveryInspection:
    execution_id: str
    classification: ExecutionRecoveryClassification
    last_sequence: int
    last_event_type: ExecutionJournalEventType | None
    client_order_id: str | None
    provider_order_id: str | None


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ExecutionJournalError("journal value is not canonical JSON") from exc


def _safe_id(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_ID.fullmatch(value) is None
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise ExecutionJournalError(f"{name} is not a safe bounded identifier")
    return value


def _iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionJournalError("journal timestamps must be timezone-aware")
    return value.isoformat()


def _payload_has_secret(value: object, parent: str = "") -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SECRET_KEYS or any(
                item in normalized for item in ("authorization", "access_token", "api_key")
            ):
                return True
            if _payload_has_secret(child, normalized):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_payload_has_secret(item, parent) for item in value)
    return False


def _terms(proposal: GovernedEquityTradeProposal) -> dict[str, object]:
    return {
        "account_binding": proposal.account_binding,
        "correlation_id": proposal.correlation_id,
        "instrument_type": proposal.instrument_type,
        "limit_price": proposal.limit_price,
        "notional_amount": proposal.notional_amount,
        "order_type": proposal.order_type,
        "proposal_hash": proposal.proposal_hash,
        "provider": PUBLIC_EXECUTION_PROVIDER_ID,
        "quantity": proposal.quantity,
        "side": proposal.side,
        "symbol": proposal.symbol,
        "time_in_force": proposal.time_in_force,
    }


_ALLOWED_PREVIOUS: dict[ExecutionJournalEventType, frozenset[ExecutionJournalEventType | None]] = {
    ExecutionJournalEventType.PROPOSAL_CREATED: frozenset({None}),
    ExecutionJournalEventType.PORTFOLIO_SNAPSHOT_BOUND: frozenset(
        {ExecutionJournalEventType.PROPOSAL_CREATED}
    ),
    ExecutionJournalEventType.PREFLIGHT_RESULT_BOUND: frozenset(
        {ExecutionJournalEventType.PORTFOLIO_SNAPSHOT_BOUND}
    ),
    ExecutionJournalEventType.APPROVAL_CONSUMED: frozenset(
        {ExecutionJournalEventType.PREFLIGHT_RESULT_BOUND}
    ),
    ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED: frozenset(
        {ExecutionJournalEventType.APPROVAL_CONSUMED}
    ),
    ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED: frozenset(
        {
            ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
            ExecutionJournalEventType.SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION,
            ExecutionJournalEventType.RECONCILIATION_ATTEMPTED,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
        }
    ),
    ExecutionJournalEventType.SUBMISSION_OUTCOME_AMBIGUOUS: frozenset(
        {ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED}
    ),
    ExecutionJournalEventType.SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION: frozenset(
        {ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED}
    ),
    ExecutionJournalEventType.BROKER_ORDER_ID_ASSOCIATED: frozenset(
        {
            ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
        }
    ),
    ExecutionJournalEventType.RECONCILIATION_ATTEMPTED: frozenset(
        {
            ExecutionJournalEventType.BROKER_ORDER_ID_ASSOCIATED,
            ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED,
            ExecutionJournalEventType.SUBMISSION_OUTCOME_AMBIGUOUS,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
            ExecutionJournalEventType.CANCELLATION_OUTCOME_AMBIGUOUS,
            ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED,
        }
    ),
    ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED: frozenset(
        {ExecutionJournalEventType.RECONCILIATION_ATTEMPTED}
    ),
    ExecutionJournalEventType.TERMINAL_ORDER_STATUS_OBSERVED: frozenset(
        {
            ExecutionJournalEventType.BROKER_ORDER_ID_ASSOCIATED,
            ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED,
            ExecutionJournalEventType.RECONCILIATION_ATTEMPTED,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
            ExecutionJournalEventType.CANCELLATION_RECONCILIATION_COMPLETED,
        }
    ),
    ExecutionJournalEventType.CANCELLATION_PROPOSAL_CREATED: frozenset(
        {
            ExecutionJournalEventType.BROKER_ORDER_ID_ASSOCIATED,
            ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
        }
    ),
    ExecutionJournalEventType.CANCELLATION_APPROVAL_CONSUMED: frozenset(
        {ExecutionJournalEventType.CANCELLATION_PROPOSAL_CREATED}
    ),
    ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED: frozenset(
        {ExecutionJournalEventType.CANCELLATION_APPROVAL_CONSUMED}
    ),
    ExecutionJournalEventType.CANCELLATION_ACKNOWLEDGED: frozenset(
        {ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED}
    ),
    ExecutionJournalEventType.CANCELLATION_OUTCOME_AMBIGUOUS: frozenset(
        {ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED}
    ),
    ExecutionJournalEventType.CANCELLATION_RECONCILIATION_COMPLETED: frozenset(
        {ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED}
    ),
    ExecutionJournalEventType.PERMANENTLY_REJECTED: frozenset(
        {
            ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
            ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED,
            ExecutionJournalEventType.BROKER_ORDER_ID_ASSOCIATED,
        }
    ),
    ExecutionJournalEventType.QUARANTINED: frozenset(ExecutionJournalEventType),
}


class DurableExecutionJournal:
    """Caller-supplied, immutable, hash-chained local execution repository."""

    def __init__(
        self,
        root: Path,
        *,
        max_record_bytes: int = MAX_RECORD_BYTES,
        max_execution_records: int = MAX_EXECUTION_RECORDS,
        max_repository_records: int = MAX_REPOSITORY_RECORDS,
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ExecutionJournalError("journal root must be an absolute Path")
        if root.is_symlink() or not root.exists() or not root.is_dir():
            raise ExecutionJournalError("journal root must be an existing non-symlink directory")
        if not 1_024 <= max_record_bytes <= MAX_RECORD_BYTES:
            raise ExecutionJournalError("record byte bound is invalid")
        if not 1 <= max_execution_records <= MAX_EXECUTION_RECORDS:
            raise ExecutionJournalError("execution record bound is invalid")
        if not 1 <= max_repository_records <= MAX_REPOSITORY_RECORDS:
            raise ExecutionJournalError("repository record bound is invalid")
        self.root = root
        self._records = root / "executions"
        self._lock_path = root / ".journal.lock"
        self._max_record_bytes = max_record_bytes
        self._max_execution_records = max_execution_records
        self._max_repository_records = max_repository_records
        self._initialize()

    def _initialize(self) -> None:
        allowed = {"executions", ".journal.lock"}
        unexpected = sorted(item.name for item in self.root.iterdir() if item.name not in allowed)
        if unexpected:
            raise ExecutionJournalCorruptionError(
                f"unexpected journal repository entries: {', '.join(unexpected)}"
            )
        if self._records.exists() and (self._records.is_symlink() or not self._records.is_dir()):
            raise ExecutionJournalCorruptionError("execution records path is unsafe")
        if not self._records.exists():
            self._records.mkdir(mode=0o700)
            self._fsync_directory(self.root)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.audit()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _execution_dir(self, execution_id: str, *, create: bool) -> Path:
        execution_id = _safe_id(execution_id, "execution_id")
        path = self._records / execution_id
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise ExecutionJournalCorruptionError("execution path is unsafe")
        elif create:
            try:
                path.mkdir(mode=0o700)
                self._fsync_directory(self._records)
            except OSError as exc:
                raise ExecutionJournalError(
                    "durable journal directory commit failed"
                ) from exc
        return path

    def _load(self, execution_id: str) -> tuple[ExecutionJournalEvent, ...]:
        path = self._execution_dir(execution_id, create=False)
        if not path.exists():
            return ()
        events: list[ExecutionJournalEvent] = []
        for item in sorted(path.iterdir(), key=lambda candidate: candidate.name):
            if item.is_symlink() or not item.is_file():
                raise ExecutionJournalCorruptionError("unexpected execution journal entry")
            match = _RECORD_NAME.fullmatch(item.name)
            if match is None:
                raise ExecutionJournalCorruptionError("unexpected execution journal filename")
            try:
                raw = item.read_bytes()
            except OSError as exc:
                raise ExecutionJournalCorruptionError("journal record is unreadable") from exc
            if not raw or len(raw) > self._max_record_bytes or not raw.endswith(b"\n"):
                raise ExecutionJournalCorruptionError("journal record is truncated or oversized")
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ExecutionJournalCorruptionError("journal record is malformed") from exc
            event = self._decode(value)
            expected_sequence = len(events) + 1
            expected_previous = events[-1].entry_hash if events else _ZERO_HASH
            if int(match.group(1)) != event.sequence or match.group(2) != event.entry_hash:
                raise ExecutionJournalCorruptionError("journal filename does not match content")
            if event.sequence != expected_sequence:
                raise ExecutionJournalCorruptionError("journal sequence is missing or duplicated")
            if event.execution_id != execution_id:
                raise ExecutionJournalCorruptionError("cross-execution record injection detected")
            if event.previous_entry_hash != expected_previous:
                raise ExecutionJournalCorruptionError("journal previous-hash link is broken")
            self._validate_transition(events, event)
            events.append(event)
        return tuple(events)

    def _decode(self, value: object) -> ExecutionJournalEvent:
        if not isinstance(value, dict) or set(value) != _RECORD_KEYS:
            raise ExecutionJournalCorruptionError("journal record schema is invalid")
        if value["journal_version"] != JOURNAL_VERSION:
            raise ExecutionJournalCorruptionError("unsupported journal version")
        material = {key: value[key] for key in _RECORD_KEYS if key != "entry_hash"}
        expected = sha256(_canonical(material)).hexdigest()
        if value["entry_hash"] != expected or _HASH.fullmatch(str(value["entry_hash"])) is None:
            raise ExecutionJournalCorruptionError("journal entry hash is invalid")
        try:
            created_at = datetime.fromisoformat(value["created_at"])
            event_type = ExecutionJournalEventType(value["event_type"])
        except (TypeError, ValueError) as exc:
            raise ExecutionJournalCorruptionError("journal record values are invalid") from exc
        if created_at.tzinfo is None or not isinstance(value["sequence"], int):
            raise ExecutionJournalCorruptionError("journal timestamp or sequence is invalid")
        if not isinstance(value["payload"], dict) or _payload_has_secret(value["payload"]):
            raise ExecutionJournalCorruptionError("journal payload is invalid or secret-bearing")
        return ExecutionJournalEvent(
            journal_version=JOURNAL_VERSION,
            execution_id=_safe_id(value["execution_id"], "execution_id"),
            sequence=value["sequence"],
            event_type=event_type,
            payload=value["payload"],
            previous_entry_hash=value["previous_entry_hash"],
            entry_hash=value["entry_hash"],
            created_at=created_at,
        )

    def _validate_transition(
        self, history: list[ExecutionJournalEvent] | tuple[ExecutionJournalEvent, ...], event: ExecutionJournalEvent
    ) -> None:
        previous = history[-1].event_type if history else None
        if previous not in _ALLOWED_PREVIOUS[event.event_type]:
            raise ExecutionJournalCorruptionError("impossible execution journal transition")
        if history:
            first_terms = history[0].payload.get("order_terms")
            current_terms = event.payload.get("order_terms")
            if current_terms is not None and current_terms != first_terms:
                raise ExecutionJournalCorruptionError("approved order terms changed")
        approval_id = event.payload.get("approval_id")
        if approval_id is not None:
            for prior in history:
                if (
                    prior.payload.get("approval_id") == approval_id
                    and prior.event_type
                    in {
                        ExecutionJournalEventType.APPROVAL_CONSUMED,
                        ExecutionJournalEventType.CANCELLATION_APPROVAL_CONSUMED,
                    }
                    and prior.event_type == event.event_type
                    and event.event_type
                    in {
                        ExecutionJournalEventType.APPROVAL_CONSUMED,
                        ExecutionJournalEventType.CANCELLATION_APPROVAL_CONSUMED,
                    }
                ):
                    raise ExecutionJournalCorruptionError("approval identity was reused")

    def _append(
        self,
        execution_id: str,
        event_type: ExecutionJournalEventType,
        payload: Mapping[str, object],
        created_at: datetime,
    ) -> ExecutionJournalEvent:
        if not isinstance(event_type, ExecutionJournalEventType):
            raise ExecutionJournalError("event type must be governed")
        payload = dict(payload)
        if _payload_has_secret(payload):
            raise ExecutionJournalError("secret-bearing journal payload rejected")
        if len(_canonical(payload)) > self._max_record_bytes // 2:
            raise ExecutionJournalError("journal payload exceeds configured bound")
        with self._locked():
            history = list(self._load(execution_id))
            if len(history) >= self._max_execution_records:
                raise ExecutionJournalError("execution journal capacity exceeded")
            total = sum(len(self._load(item.name)) for item in self._records.iterdir())
            if total >= self._max_repository_records:
                raise ExecutionJournalError("journal repository capacity exceeded")
            sequence = len(history) + 1
            material = {
                "created_at": _iso(created_at),
                "event_type": event_type.value,
                "execution_id": execution_id,
                "journal_version": JOURNAL_VERSION,
                "payload": payload,
                "previous_entry_hash": history[-1].entry_hash if history else _ZERO_HASH,
                "sequence": sequence,
            }
            entry_hash = sha256(_canonical(material)).hexdigest()
            value = {**material, "entry_hash": entry_hash}
            event = self._decode(value)
            if history and history[-1].event_type == event_type:
                if history[-1].payload == payload:
                    return history[-1]
                raise ExecutionJournalConflictError("conflicting duplicate journal append")
            self._validate_transition(history, event)
            directory = self._execution_dir(execution_id, create=True)
            target = directory / f"{sequence:08d}-{entry_hash}.json"
            encoded = _canonical(value) + b"\n"
            if len(encoded) > self._max_record_bytes:
                raise ExecutionJournalError("journal record exceeds configured bound")
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)
                self._fsync_directory(directory)
            except FileExistsError as exc:
                raise ExecutionJournalConflictError("concurrent journal append conflict") from exc
            except OSError as exc:
                raise ExecutionJournalError("durable journal commit failed") from exc
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            return event

    def record_proposal(self, proposal: GovernedEquityTradeProposal) -> None:
        self._append(
            proposal.proposal_id,
            ExecutionJournalEventType.PROPOSAL_CREATED,
            {"order_terms": _terms(proposal), "proposal_id": proposal.proposal_id},
            proposal.created_at,
        )

    def record_portfolio_snapshot(
        self, proposal: GovernedEquityTradeProposal, snapshot: PublicPortfolioSnapshot
    ) -> None:
        self._append(
            proposal.proposal_id,
            ExecutionJournalEventType.PORTFOLIO_SNAPSHOT_BOUND,
            {
                "order_terms": _terms(proposal),
                "portfolio_snapshot_digest": snapshot.response_hash,
            },
            snapshot.acquired_at,
        )

    def record_preflight(
        self, proposal: GovernedEquityTradeProposal, preflight: PublicPreflightRecord
    ) -> None:
        self._append(
            proposal.proposal_id,
            ExecutionJournalEventType.PREFLIGHT_RESULT_BOUND,
            {
                "order_terms": _terms(proposal),
                "preflight_digest": preflight.preflight_hash,
                "preflight_id": preflight.preflight_id,
            },
            preflight.acquired_at,
        )

    def record_intent(
        self, intent: PublicSubmissionIntent, approval: GovernedTradeApproval
    ) -> None:
        history = self._load(intent.proposal_id)
        terms = history[0].payload["order_terms"] if history else None
        common = {
            "approval_digest": approval.approval_hash,
            "approval_id": approval.approval_id,
            "client_order_id": intent.order_id,
            "correlation_id": intent.correlation_id,
            "order_terms": terms,
            "preflight_digest": intent.preflight_hash,
            "provider": PUBLIC_EXECUTION_PROVIDER_ID,
        }
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.APPROVAL_CONSUMED,
            common,
            approval.approved_at,
        )
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
            {**common, "body_hash": intent.body_hash, "intent_id": intent.intent_id},
            intent.recorded_at,
        )

    def intent(self, intent_id: str) -> PublicSubmissionIntent:
        for execution_id in self.execution_ids():
            for event in self._load(execution_id):
                if event.event_type == ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED:
                    payload = event.payload
                    if payload.get("intent_id") == intent_id:
                        terms = payload["order_terms"]
                        return PublicSubmissionIntent(
                            intent_id=intent_id,
                            order_id=payload["client_order_id"],
                            proposal_id=execution_id,
                            proposal_hash=terms["proposal_hash"],
                            preflight_id=next(
                                item.payload["preflight_id"]
                                for item in self._load(execution_id)
                                if item.event_type == ExecutionJournalEventType.PREFLIGHT_RESULT_BOUND
                            ),
                            preflight_hash=payload["preflight_digest"],
                            approval_id=payload["approval_id"],
                            approval_hash=payload["approval_digest"],
                            account_binding=terms["account_binding"],
                            body_hash=payload["body_hash"],
                            recorded_at=event.created_at,
                            correlation_id=payload["correlation_id"],
                        )
        raise FinancialDataValidationError("unknown Public submission intent")

    def save_execution(self, execution: PublicOrderExecution) -> None:
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        last = history[-1].event_type
        payload = {
            "client_order_id": execution.order_id,
            "correlation_id": intent.correlation_id,
            "order_terms": history[0].payload["order_terms"],
            "provider": PUBLIC_EXECUTION_PROVIDER_ID,
            "provider_order_id": execution.order_id,
            "response_digest": execution.response_hash,
            "state": execution.state.value,
        }
        if (
            last
            in {
                ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
                ExecutionJournalEventType.CANCELLATION_RECONCILIATION_COMPLETED,
            }
            and history[-1].payload.get("state") == execution.state.value
        ):
            return
        if execution.state == PublicExecutionState.SUBMISSION_INTENT_RECORDED:
            return
        if execution.state == PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED:
            if last == ExecutionJournalEventType.RECONCILIATION_ATTEMPTED:
                event_type = ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED
                payload["broker_order_exists"] = False
            else:
                event_type = (
                    ExecutionJournalEventType.CANCELLATION_OUTCOME_AMBIGUOUS
                    if last == ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED
                    else ExecutionJournalEventType.SUBMISSION_OUTCOME_AMBIGUOUS
                )
        elif execution.state in {
            PublicExecutionState.FILLED,
            PublicExecutionState.CANCELLED,
            PublicExecutionState.EXPIRED,
        }:
            event_type = ExecutionJournalEventType.TERMINAL_ORDER_STATUS_OBSERVED
        elif execution.state == PublicExecutionState.REJECTED:
            event_type = ExecutionJournalEventType.PERMANENTLY_REJECTED
        elif (
            last == ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED
            and execution.state == PublicExecutionState.CANCELLATION_REQUESTED
        ):
            event_type = ExecutionJournalEventType.CANCELLATION_ACKNOWLEDGED
        elif last in {
            ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
            ExecutionJournalEventType.SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
        }:
            event_type = ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED
        else:
            event_type = ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED
        self._append(intent.proposal_id, event_type, payload, execution.updated_at)
        if event_type == ExecutionJournalEventType.BROKER_SUBMISSION_ACKNOWLEDGED:
            self._append(
                intent.proposal_id,
                ExecutionJournalEventType.BROKER_ORDER_ID_ASSOCIATED,
                payload,
                execution.updated_at,
            )

    def execution(self, order_id: str) -> PublicOrderExecution:
        for execution_id in self.execution_ids():
            events = self._load(execution_id)
            intent_events = [
                item
                for item in events
                if item.event_type == ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED
                and item.payload.get("client_order_id") == order_id
            ]
            if not intent_events:
                continue
            intent = intent_events[0]
            state = PublicExecutionState.SUBMISSION_INTENT_RECORDED
            response_hash = None
            for event in events[len(events[: events.index(intent) + 1]) :]:
                value = event.payload.get("state")
                if isinstance(value, str):
                    state = PublicExecutionState(value)
                    response_hash = event.payload.get("response_digest")
            return PublicOrderExecution(
                order_id=order_id,
                intent_id=intent.payload["intent_id"],
                account_binding=intent.payload["order_terms"]["account_binding"],
                state=state,
                updated_at=events[-1].created_at,
                response_hash=response_hash,
            )
        raise FinancialDataValidationError("unknown Public order")

    def consume_cancellation(self, approval: PublicCancellationApproval) -> None:
        execution = self.execution(approval.order_id)
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        if approval.account_binding != history[0].payload["order_terms"]["account_binding"]:
            raise ExecutionJournalCorruptionError("approved order terms changed")
        common = {
            "approval_id": approval.approval_id,
            "client_order_id": approval.order_id,
            "correlation_id": intent.correlation_id,
            "order_terms": history[0].payload["order_terms"],
            "provider": PUBLIC_EXECUTION_PROVIDER_ID,
            "provider_order_id": approval.order_id,
        }
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.CANCELLATION_PROPOSAL_CREATED,
            common,
            approval.approved_at,
        )
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.CANCELLATION_APPROVAL_CONSUMED,
            common,
            approval.approved_at,
        )

    def record_cancellation_intent(
        self, execution: PublicOrderExecution, approval: PublicCancellationApproval, at: datetime
    ) -> None:
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED,
            {
                "approval_id": approval.approval_id,
                "cancellation_id": approval.approval_id.removeprefix(
                    "public-cancel-approval-"
                ),
                "client_order_id": execution.order_id,
                "correlation_id": intent.correlation_id,
                "order_terms": history[0].payload["order_terms"],
                "provider": PUBLIC_EXECUTION_PROVIDER_ID,
                "provider_order_id": execution.order_id,
            },
            at,
        )

    def record_reconciliation_attempt(self, execution: PublicOrderExecution, at: datetime) -> None:
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.RECONCILIATION_ATTEMPTED,
            {
                "client_order_id": execution.order_id,
                "correlation_id": intent.correlation_id,
                "order_terms": history[0].payload["order_terms"],
                "provider": PUBLIC_EXECUTION_PROVIDER_ID,
                "provider_order_id": execution.order_id,
            },
            at,
        )

    def record_reconciliation_result(
        self,
        execution: PublicOrderExecution,
        *,
        broker_order_exists: bool,
        at: datetime,
    ) -> None:
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        result = self._append(
            intent.proposal_id,
            ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED,
            {
                "broker_order_exists": broker_order_exists,
                "client_order_id": execution.order_id,
                "correlation_id": intent.correlation_id,
                "order_terms": history[0].payload["order_terms"],
                "provider": PUBLIC_EXECUTION_PROVIDER_ID,
                "provider_order_id": execution.order_id if broker_order_exists else None,
                "response_digest": execution.response_hash,
                "state": execution.state.value,
            },
            at,
        )
        cancellation = next(
            (
                item
                for item in reversed(history)
                if item.event_type
                in {
                    ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED,
                    ExecutionJournalEventType.CANCELLATION_OUTCOME_AMBIGUOUS,
                }
            ),
            None,
        )
        if cancellation is not None:
            self._append(
                intent.proposal_id,
                ExecutionJournalEventType.CANCELLATION_RECONCILIATION_COMPLETED,
                {
                    **dict(result.payload),
                    "cancellation_id": cancellation.payload.get("cancellation_id"),
                },
                at,
            )

    def submission_retry_permitted(self, intent_id: str) -> bool:
        intent = self.intent(intent_id)
        history = self._load(intent.proposal_id)
        if not history:
            return False
        last = history[-1]
        return (
            (
                last.event_type
                == ExecutionJournalEventType.SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION
                or (
                    last.event_type
                    == ExecutionJournalEventType.RECONCILIATION_RESULT_RECORDED
                    and last.payload.get("broker_order_exists") is False
                )
            )
            and last.payload.get("client_order_id") == intent.order_id
        )

    def record_submission_not_started(
        self, execution: PublicOrderExecution, *, reason: str, at: datetime
    ) -> None:
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION,
            {
                "client_order_id": execution.order_id,
                "correlation_id": intent.correlation_id,
                "diagnostic_code": reason,
                "order_terms": history[0].payload["order_terms"],
                "provider": PUBLIC_EXECUTION_PROVIDER_ID,
            },
            at,
        )

    def quarantine(
        self, execution: PublicOrderExecution, *, reason_code: str, at: datetime
    ) -> None:
        reason_code = _safe_id(reason_code, "reason_code")
        intent = self.intent(execution.intent_id)
        history = self._load(intent.proposal_id)
        self._append(
            intent.proposal_id,
            ExecutionJournalEventType.QUARANTINED,
            {
                "client_order_id": execution.order_id,
                "correlation_id": intent.correlation_id,
                "order_terms": history[0].payload["order_terms"],
                "provider": PUBLIC_EXECUTION_PROVIDER_ID,
                "reason_code": reason_code,
            },
            at,
        )

    def append_evidence(self, evidence: PublicAuditEvidence) -> None:
        # Step 9B evidence remains in its injected evidence boundary.  Durable
        # lifecycle events above contain the mutation-recovery facts.
        del evidence

    def evidence(self) -> tuple[PublicAuditEvidence, ...]:
        return ()

    def execution_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for item in sorted(self._records.iterdir(), key=lambda candidate: candidate.name):
            if item.is_symlink() or not item.is_dir() or _SAFE_ID.fullmatch(item.name) is None:
                raise ExecutionJournalCorruptionError("unexpected execution repository entry")
            ids.append(item.name)
        return tuple(ids)

    def audit(self) -> tuple[ExecutionRecoveryInspection, ...]:
        return tuple(self.inspect(execution_id) for execution_id in self.execution_ids())

    def read_events(self, execution_id: str) -> tuple[ExecutionJournalEvent, ...]:
        """Return validated immutable history without exposing mutation primitives."""
        return self._load(execution_id)

    def inspect(self, execution_id: str) -> ExecutionRecoveryInspection:
        try:
            events = self._load(execution_id)
        except ExecutionJournalCorruptionError:
            return ExecutionRecoveryInspection(
                execution_id=execution_id,
                classification=ExecutionRecoveryClassification.CORRUPT,
                last_sequence=0,
                last_event_type=None,
                client_order_id=None,
                provider_order_id=None,
            )
        if not events:
            classification = ExecutionRecoveryClassification.SAFELY_RETRYABLE_BEFORE_SUBMISSION
            last = None
        else:
            last = events[-1]
            if last.event_type == ExecutionJournalEventType.QUARANTINED:
                classification = ExecutionRecoveryClassification.QUARANTINED
            elif last.event_type == ExecutionJournalEventType.PERMANENTLY_REJECTED:
                classification = ExecutionRecoveryClassification.REJECTED
            elif last.event_type == ExecutionJournalEventType.TERMINAL_ORDER_STATUS_OBSERVED:
                classification = ExecutionRecoveryClassification.COMPLETE
            elif (
                last.event_type
                == ExecutionJournalEventType.CANCELLATION_RECONCILIATION_COMPLETED
                and last.payload.get("state")
                in {
                    PublicExecutionState.CANCELLED.value,
                    PublicExecutionState.FILLED.value,
                    PublicExecutionState.EXPIRED.value,
                }
            ):
                classification = ExecutionRecoveryClassification.COMPLETE
            elif last.event_type in {
                ExecutionJournalEventType.CANCELLATION_INTENT_RECORDED,
                ExecutionJournalEventType.CANCELLATION_OUTCOME_AMBIGUOUS,
            }:
                classification = (
                    ExecutionRecoveryClassification.CANCELLATION_RECONCILIATION_REQUIRED
                )
            elif last.event_type in {
                ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
                ExecutionJournalEventType.SUBMISSION_OUTCOME_AMBIGUOUS,
                ExecutionJournalEventType.RECONCILIATION_ATTEMPTED,
            }:
                classification = ExecutionRecoveryClassification.RECONCILIATION_REQUIRED
            elif last.event_type in {
                ExecutionJournalEventType.SUBMISSION_TRANSPORT_FAILED_BEFORE_TRANSMISSION,
                ExecutionJournalEventType.PROPOSAL_CREATED,
                ExecutionJournalEventType.PORTFOLIO_SNAPSHOT_BOUND,
                ExecutionJournalEventType.PREFLIGHT_RESULT_BOUND,
                ExecutionJournalEventType.APPROVAL_CONSUMED,
            }:
                classification = (
                    ExecutionRecoveryClassification.SAFELY_RETRYABLE_BEFORE_SUBMISSION
                )
            else:
                classification = ExecutionRecoveryClassification.RECONCILIATION_REQUIRED
        payload = last.payload if last else {}
        return ExecutionRecoveryInspection(
            execution_id=execution_id,
            classification=classification,
            last_sequence=last.sequence if last else 0,
            last_event_type=last.event_type if last else None,
            client_order_id=payload.get("client_order_id"),
            provider_order_id=payload.get("provider_order_id"),
        )
