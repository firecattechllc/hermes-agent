"""Crash-safe append-only persistence for governed portfolio ledger events."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterator, Mapping

from sigil.integrations.providers.models import FinancialDataValidationError

from .models import (
    LEDGER_VERSION,
    PortfolioLedgerConflictError,
    PortfolioLedgerCorruptionError,
    PortfolioLedgerEntry,
    PortfolioLedgerEvent,
    PortfolioLedgerEventType,
    canonical_bytes,
    digest,
    identifier,
    timestamp,
)


MAX_RECORD_BYTES = 64_000
MAX_ACCOUNT_RECORDS = 100_000
MAX_REPOSITORY_RECORDS = 1_000_000
MAX_REPOSITORY_BYTES = 1_000_000_000
_ZERO_HASH = "0" * 64
_RECORD_NAME = re.compile(r"^([0-9]{10})-([0-9a-f]{64})\.json$", re.ASCII)
_EXPECTED_KEYS = frozenset(
    {
        "account_binding",
        "accounting_policy_version",
        "acquired_at",
        "created_at",
        "currency",
        "effective_at",
        "entry_hash",
        "event_identity",
        "event_type",
        "ledger_identity",
        "ledger_version",
        "pagination_identity",
        "payload",
        "previous_entry_hash",
        "sequence",
        "source_complete",
        "source_identity",
        "source_provider",
        "source_record_id",
        "source_response_digest",
        "source_timestamp",
        "source_truncated",
    }
)


class PortfolioLedgerRepository:
    """Caller-supplied, immutable, hash-chained local accounting repository."""

    def __init__(
        self,
        root: Path,
        *,
        max_record_bytes: int = MAX_RECORD_BYTES,
        max_account_records: int = MAX_ACCOUNT_RECORDS,
        max_repository_records: int = MAX_REPOSITORY_RECORDS,
        max_repository_bytes: int = MAX_REPOSITORY_BYTES,
        before_replace: Callable[[Path, Path], None] | None = None,
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise PortfolioLedgerCorruptionError("ledger root must be an absolute Path")
        if root.is_symlink() or not root.exists() or not root.is_dir():
            raise PortfolioLedgerCorruptionError(
                "ledger root must be an existing non-symlink directory"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                max_record_bytes,
                max_account_records,
                max_repository_records,
                max_repository_bytes,
            )
        ):
            raise PortfolioLedgerCorruptionError("repository bounds are invalid")
        self._root = root.resolve(strict=True)
        self._max_record_bytes = max_record_bytes
        self._max_account_records = max_account_records
        self._max_repository_records = max_repository_records
        self._max_repository_bytes = max_repository_bytes
        self._before_replace = before_replace

    @property
    def root(self) -> Path:
        return self._root

    def initialize_account(self, account_binding: str, ledger_identity: str) -> Path:
        identifier(account_binding, "account_binding")
        identifier(ledger_identity, "ledger_identity")
        account_dir = self._account_dir(account_binding, ledger_identity)
        with self._repository_lock(exclusive=True):
            self._audit_root()
            if account_dir.exists():
                if account_dir.is_symlink() or not account_dir.is_dir():
                    raise PortfolioLedgerCorruptionError("account ledger path is unsafe")
                self._audit_account_dir(account_dir)
                return account_dir
            account_dir.mkdir(mode=0o700)
            self._fsync_directory(self._root)
        return account_dir

    def append(self, event: PortfolioLedgerEvent, *, created_at: datetime) -> PortfolioLedgerEntry:
        created_at = timestamp(created_at, "created_at")
        account_dir = self.initialize_account(event.account_binding, event.ledger_identity)
        with self._repository_lock(exclusive=True), self._account_lock(
            account_dir, exclusive=True
        ):
            self._audit_root()
            entries = self._load_account(account_dir, event.account_binding, event.ledger_identity)
            source_matches = [
                item
                for item in entries
                if (
                    item.event.source_provider,
                    item.event.source_record_id,
                )
                == (event.source_provider, event.source_record_id)
            ]
            if source_matches:
                exact = next(
                    (item for item in source_matches if item.event.event_identity == event.event_identity),
                    None,
                )
                if exact is not None:
                    return exact
                raise PortfolioLedgerConflictError("conflicting duplicate source activity")
            if any(item.event.event_identity == event.event_identity for item in entries):
                return next(
                    item for item in entries if item.event.event_identity == event.event_identity
                )
            if len(entries) >= self._max_account_records:
                raise PortfolioLedgerConflictError("maximum account ledger length exceeded")
            count, size = self._repository_usage()
            if count >= self._max_repository_records:
                raise PortfolioLedgerConflictError("maximum repository record count exceeded")
            self._reject_closed_period_activity(entries, event)
            self._validate_governed_adjustment(entries, event)
            sequence = len(entries) + 1
            previous_hash = entries[-1].entry_hash if entries else _ZERO_HASH
            body = self._record_body(event, sequence, previous_hash, created_at)
            entry_hash = sha256(canonical_bytes(body)).hexdigest()
            record = {**body, "entry_hash": entry_hash}
            raw = canonical_bytes(record)
            if len(raw) > self._max_record_bytes:
                raise PortfolioLedgerConflictError("maximum ledger record size exceeded")
            if size + len(raw) > self._max_repository_bytes:
                raise PortfolioLedgerConflictError("maximum repository byte size exceeded")
            target = account_dir / f"{sequence:010d}-{entry_hash}.json"
            self._atomic_write(account_dir, target, raw)
            return self._entry_from_record(record)

    def read_entries(
        self, account_binding: str, ledger_identity: str
    ) -> tuple[PortfolioLedgerEntry, ...]:
        account_dir = self._account_dir(account_binding, ledger_identity)
        with self._repository_lock(exclusive=False):
            self._audit_root()
            if not account_dir.exists():
                return ()
            with self._account_lock(account_dir, exclusive=False):
                return self._load_account(account_dir, account_binding, ledger_identity)

    def audit(
        self, account_binding: str, ledger_identity: str
    ) -> tuple[PortfolioLedgerEntry, ...]:
        return self.read_entries(account_binding, ledger_identity)

    def get_event(
        self, account_binding: str, ledger_identity: str, event_identity: str
    ) -> PortfolioLedgerEntry | None:
        digest(event_identity, "event_identity")
        return next(
            (
                entry
                for entry in self.read_entries(account_binding, ledger_identity)
                if entry.event.event_identity == event_identity
            ),
            None,
        )

    def find_source_record(
        self,
        account_binding: str,
        ledger_identity: str,
        source_provider: str,
        source_record_id: str,
    ) -> tuple[PortfolioLedgerEntry, ...]:
        return tuple(
            entry
            for entry in self.read_entries(account_binding, ledger_identity)
            if entry.event.source_provider == source_provider
            and entry.event.source_record_id == source_record_id
        )

    def source_coverage_summary(
        self, account_binding: str, ledger_identity: str
    ) -> tuple[tuple[str, int, bool, bool], ...]:
        summary: dict[str, list[object]] = {}
        for entry in self.read_entries(account_binding, ledger_identity):
            values = summary.setdefault(entry.event.source_provider, [0, True, False])
            values[0] = int(values[0]) + 1
            values[1] = bool(values[1]) and entry.event.source_complete
            values[2] = bool(values[2]) or entry.event.source_truncated
        return tuple(
            (provider, int(values[0]), bool(values[1]), bool(values[2]))
            for provider, values in sorted(summary.items())
        )

    def _account_dir(self, account_binding: str, ledger_identity: str) -> Path:
        safe_account = identifier(account_binding, "account_binding")
        safe_ledger = identifier(ledger_identity, "ledger_identity")
        result = self._root / f"{safe_account}--{safe_ledger}"
        if result.parent.resolve(strict=True) != self._root:
            raise PortfolioLedgerCorruptionError("ledger path traversal is forbidden")
        return result

    @contextmanager
    def _repository_lock(self, *, exclusive: bool) -> Iterator[None]:
        lock_path = self._root / ".ledger.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def _account_lock(self, account_dir: Path, *, exclusive: bool) -> Iterator[None]:
        if account_dir.is_symlink() or not account_dir.is_dir():
            raise PortfolioLedgerCorruptionError("account ledger path is unsafe")
        lock_path = account_dir / ".account.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _audit_root(self) -> None:
        if self._root.is_symlink() or not self._root.is_dir():
            raise PortfolioLedgerCorruptionError("ledger root changed or is unsafe")
        for path in self._root.iterdir():
            if path.name == ".ledger.lock":
                if path.is_symlink() or not path.is_file():
                    raise PortfolioLedgerCorruptionError("repository lock is unsafe")
                continue
            if path.is_symlink() or not path.is_dir() or "--" not in path.name:
                raise PortfolioLedgerCorruptionError(
                    f"unexpected repository file: {path.name}"
                )

    def _audit_account_dir(self, account_dir: Path) -> None:
        for path in account_dir.iterdir():
            if path.name == ".account.lock":
                if path.is_symlink() or not path.is_file():
                    raise PortfolioLedgerCorruptionError("account lock is unsafe")
                continue
            if path.is_symlink() or not path.is_file() or _RECORD_NAME.fullmatch(path.name) is None:
                raise PortfolioLedgerCorruptionError(
                    f"unexpected account ledger file: {path.name}"
                )

    def _load_account(
        self, account_dir: Path, account_binding: str, ledger_identity: str
    ) -> tuple[PortfolioLedgerEntry, ...]:
        self._audit_account_dir(account_dir)
        records = sorted(
            (path for path in account_dir.iterdir() if path.name != ".account.lock"),
            key=lambda path: path.name,
        )
        entries: list[PortfolioLedgerEntry] = []
        previous_hash = _ZERO_HASH
        seen_sources: dict[tuple[str, str], str] = {}
        for expected_sequence, path in enumerate(records, start=1):
            match = _RECORD_NAME.fullmatch(path.name)
            assert match is not None
            if int(match.group(1)) != expected_sequence:
                raise PortfolioLedgerCorruptionError("ledger sequence gap or reorder detected")
            try:
                raw = path.read_bytes()
                if not raw or len(raw) > self._max_record_bytes:
                    raise PortfolioLedgerCorruptionError("ledger record size is invalid")
                record = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PortfolioLedgerCorruptionError("ledger record is truncated or malformed") from exc
            if not isinstance(record, dict) or frozenset(record) != _EXPECTED_KEYS:
                raise PortfolioLedgerCorruptionError("ledger record schema is invalid")
            if canonical_bytes(record) != raw:
                raise PortfolioLedgerCorruptionError("ledger record is not canonical JSON")
            if record["ledger_version"] != LEDGER_VERSION:
                raise PortfolioLedgerCorruptionError("unsupported ledger version")
            if record["sequence"] != expected_sequence:
                raise PortfolioLedgerCorruptionError("ledger sequence mismatch")
            if record["account_binding"] != account_binding:
                raise PortfolioLedgerCorruptionError("cross-account ledger injection")
            if record["ledger_identity"] != ledger_identity:
                raise PortfolioLedgerCorruptionError("cross-ledger injection")
            if record["previous_entry_hash"] != previous_hash:
                raise PortfolioLedgerCorruptionError("broken ledger hash link")
            entry_hash = record["entry_hash"]
            digest(entry_hash, "entry_hash")
            body = {key: value for key, value in record.items() if key != "entry_hash"}
            if sha256(canonical_bytes(body)).hexdigest() != entry_hash:
                raise PortfolioLedgerCorruptionError("ledger payload or hash was modified")
            if match.group(2) != entry_hash:
                raise PortfolioLedgerCorruptionError("ledger filename hash mismatch")
            entry = self._entry_from_record(record)
            source_key = (entry.event.source_provider, entry.event.source_record_id)
            if source_key in seen_sources:
                raise PortfolioLedgerCorruptionError("duplicate source activity in ledger")
            seen_sources[source_key] = entry.event.event_identity
            entries.append(entry)
            previous_hash = entry_hash
        return tuple(entries)

    @staticmethod
    def _record_body(
        event: PortfolioLedgerEvent,
        sequence: int,
        previous_hash: str,
        created_at: datetime,
    ) -> dict[str, object]:
        return {
            "account_binding": event.account_binding,
            "accounting_policy_version": event.accounting_policy_version,
            "acquired_at": event.acquired_at.isoformat(),
            "created_at": created_at.isoformat(),
            "currency": event.currency,
            "effective_at": event.effective_at.isoformat(),
            "event_identity": event.event_identity,
            "event_type": event.event_type.value,
            "ledger_identity": event.ledger_identity,
            "ledger_version": LEDGER_VERSION,
            "pagination_identity": event.pagination_identity,
            "payload": event.payload,
            "previous_entry_hash": previous_hash,
            "sequence": sequence,
            "source_complete": event.source_complete,
            "source_identity": event.source_identity,
            "source_provider": event.source_provider,
            "source_record_id": event.source_record_id,
            "source_response_digest": event.source_response_digest,
            "source_timestamp": event.source_timestamp.isoformat(),
            "source_truncated": event.source_truncated,
        }

    @staticmethod
    def _entry_from_record(record: Mapping[str, object]) -> PortfolioLedgerEntry:
        try:
            event = PortfolioLedgerEvent(
                account_binding=str(record["account_binding"]),
                ledger_identity=str(record["ledger_identity"]),
                event_type=PortfolioLedgerEventType(str(record["event_type"])),
                source_identity=str(record["source_identity"]),
                source_provider=str(record["source_provider"]),
                source_record_id=str(record["source_record_id"]),
                source_response_digest=str(record["source_response_digest"]),
                source_timestamp=timestamp(record["source_timestamp"], "source_timestamp"),
                effective_at=timestamp(record["effective_at"], "effective_at"),
                acquired_at=timestamp(record["acquired_at"], "acquired_at"),
                currency=str(record["currency"]),
                payload=record["payload"],  # type: ignore[arg-type]
                accounting_policy_version=str(record["accounting_policy_version"]),
                source_complete=record["source_complete"] is True,
                source_truncated=record["source_truncated"] is True,
                pagination_identity=(
                    None
                    if record["pagination_identity"] is None
                    else str(record["pagination_identity"])
                ),
                event_identity=str(record["event_identity"]),
            )
            return PortfolioLedgerEntry(
                ledger_version=int(record["ledger_version"]),  # type: ignore[arg-type]
                account_binding=event.account_binding,
                ledger_identity=event.ledger_identity,
                sequence=int(record["sequence"]),  # type: ignore[arg-type]
                event=event,
                previous_entry_hash=str(record["previous_entry_hash"]),
                entry_hash=str(record["entry_hash"]),
                created_at=timestamp(record["created_at"], "created_at"),
            )
        except (KeyError, TypeError, ValueError, FinancialDataValidationError) as exc:
            raise PortfolioLedgerCorruptionError("ledger record validation failed") from exc

    def _atomic_write(self, account_dir: Path, target: Path, raw: bytes) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=account_dir)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if self._before_replace is not None:
                self._before_replace(temporary, target)
            if target.exists():
                raise PortfolioLedgerConflictError("ledger sequence already committed")
            os.replace(temporary, target)
            self._fsync_directory(account_dir)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                raise

    def _repository_usage(self) -> tuple[int, int]:
        count = 0
        size = 0
        for account_dir in self._root.iterdir():
            if account_dir.name == ".ledger.lock":
                continue
            for path in account_dir.iterdir():
                if path.name == ".account.lock":
                    continue
                count += 1
                size += path.stat().st_size
        return count, size

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _reject_closed_period_activity(
        entries: tuple[PortfolioLedgerEntry, ...], event: PortfolioLedgerEvent
    ) -> None:
        closed: dict[str, tuple[datetime, datetime]] = {}
        for entry in entries:
            payload = entry.event.payload
            if entry.event.event_type is PortfolioLedgerEventType.ACCOUNTING_PERIOD_CLOSED:
                close_id = str(payload["close_identity"])
                closed[close_id] = (
                    timestamp(payload["period_start"], "period_start"),
                    timestamp(payload["period_end"], "period_end"),
                )
            elif entry.event.event_type is PortfolioLedgerEventType.ACCOUNTING_PERIOD_REOPENED:
                closed.pop(str(payload["close_identity"]), None)
        if event.event_type is PortfolioLedgerEventType.ACCOUNTING_PERIOD_REOPENED:
            close_id = str(event.payload.get("close_identity", ""))
            if close_id not in closed:
                raise PortfolioLedgerConflictError("period reopen does not identify an active close")
            return
        if event.event_type is PortfolioLedgerEventType.ACCOUNTING_PERIOD_CLOSED:
            return
        if any(start <= event.effective_at <= end for start, end in closed.values()):
            raise PortfolioLedgerConflictError("activity falls within a closed accounting period")

    @staticmethod
    def _validate_governed_adjustment(
        entries: tuple[PortfolioLedgerEntry, ...], event: PortfolioLedgerEvent
    ) -> None:
        by_identity = {entry.event.event_identity: entry.event for entry in entries}
        if event.event_type is PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_APPROVED:
            proposal_identity = str(event.payload.get("proposal_identity", ""))
            proposal = by_identity.get(proposal_identity)
            if (
                proposal is None
                or proposal.event_type
                is not PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_PROPOSED
                or proposal.account_binding != event.account_binding
            ):
                raise PortfolioLedgerConflictError(
                    "adjustment approval has no matching committed proposal"
                )
            approval_id = event.payload.get("approval_id")
            if any(
                entry.event.event_type
                is PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_APPROVED
                and entry.event.payload.get("approval_id") == approval_id
                for entry in entries
            ):
                raise PortfolioLedgerConflictError("adjustment approval was already consumed")
        if event.event_type is PortfolioLedgerEventType.CASH_ADJUSTMENT:
            proposal_identity = str(event.payload.get("proposal_event_identity", ""))
            proposal = by_identity.get(proposal_identity)
            approvals = [
                entry.event
                for entry in entries
                if entry.event.event_type
                is PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_APPROVED
                and entry.event.payload.get("proposal_identity") == proposal_identity
            ]
            if (
                proposal is None
                or proposal.event_type
                is not PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_PROPOSED
                or len(approvals) != 1
                or approvals[0].payload.get("approval_id")
                != event.payload.get("approval_id")
                or approvals[0].payload.get("approval_digest")
                != event.payload.get("approval_digest")
                or proposal.payload.get("amount") != event.payload.get("amount")
                or proposal.payload.get("reason_code") != event.payload.get("reason_code")
            ):
                raise PortfolioLedgerConflictError(
                    "cash adjustment lacks exact committed proposal and approval"
                )
            if any(
                entry.event.event_type is PortfolioLedgerEventType.CASH_ADJUSTMENT
                and entry.event.payload.get("proposal_event_identity") == proposal_identity
                for entry in entries
            ):
                raise PortfolioLedgerConflictError("adjustment proposal was already applied")
