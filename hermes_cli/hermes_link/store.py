"""Checksummed append-only inbox/outbox and audit persistence."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import DeliveryState, HermesLinkEnvelope, utc_now

ALLOWED_TRANSITIONS = {
    DeliveryState.QUEUED: {
        DeliveryState.DELIVERED,
        DeliveryState.ACKNOWLEDGED,
        DeliveryState.FAILED,
        DeliveryState.REJECTED,
        DeliveryState.RETRYABLE,
    },
    DeliveryState.DELIVERED: {
        DeliveryState.ACKNOWLEDGED,
        DeliveryState.FAILED,
        DeliveryState.RETRYABLE,
    },
    DeliveryState.RETRYABLE: {
        DeliveryState.DELIVERED,
        DeliveryState.FAILED,
        DeliveryState.RETRYABLE,
        DeliveryState.DEAD_LETTERED,
    },
    DeliveryState.FAILED: {DeliveryState.RETRYABLE, DeliveryState.DEAD_LETTERED},
    DeliveryState.ACKNOWLEDGED: set(),
    DeliveryState.REJECTED: set(),
    DeliveryState.DEAD_LETTERED: set(),
}


class LinkJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sequence: int = Field(..., ge=1)
    envelope: HermesLinkEnvelope
    state: DeliveryState
    recorded_at: int = Field(default_factory=utc_now, ge=0)
    reason_code: Optional[str] = Field(default=None, max_length=128)
    previous_checksum: Optional[str] = Field(default=None, min_length=64, max_length=64)
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(**values: object) -> str:
        return hashlib.sha256(
            json.dumps(
                values, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        sequence: int,
        envelope: HermesLinkEnvelope,
        state: DeliveryState,
        recorded_at: int,
        reason_code: Optional[str],
        previous_checksum: Optional[str],
    ) -> "LinkJournalRecord":
        values = {
            "sequence": sequence,
            "envelope": envelope.model_dump(mode="json"),
            "state": state.value,
            "recorded_at": recorded_at,
            "reason_code": reason_code,
            "previous_checksum": previous_checksum,
        }
        return cls(**values, checksum=cls.calculate_checksum(**values))

    @model_validator(mode="after")
    def integrity(self) -> "LinkJournalRecord":
        values = self.model_dump(mode="json", exclude={"checksum"})
        if self.checksum != self.calculate_checksum(**values):
            raise ValueError("Hermes-link journal checksum mismatch")
        if self.envelope.delivery_state != self.state:
            raise ValueError("Hermes-link journal state association mismatch")
        return self


class HermesLinkStore:
    def __init__(self, root: Path, *, capacity: int = 10000) -> None:
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.root / "hermes-link.jsonl"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        with self._thread_lock, (self.root / ".hermes-link.lock").open("a+") as handle:
            os.chmod(handle.name, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(
        self,
        envelope: HermesLinkEnvelope,
        *,
        state: Optional[DeliveryState] = None,
        reason_code: Optional[str] = None,
        recorded_at: Optional[int] = None,
    ) -> HermesLinkEnvelope:
        target = state or envelope.delivery_state
        updated = envelope.model_copy(update={"delivery_state": target})
        with self._lock():
            records = self._read_unlocked(recover_torn_tail=True)
            latest = self._latest(records).get(updated.message_id)
            if (
                latest
                and latest.envelope == updated
                and latest.reason_code == reason_code
            ):
                return updated
            if latest and latest.envelope.model_dump(
                exclude={"delivery_state", "retry"}
            ) != updated.model_dump(exclude={"delivery_state", "retry"}):
                raise ValueError("message identity collision")
            if (
                latest
                and target != latest.state
                and target not in ALLOWED_TRANSITIONS[latest.state]
            ):
                raise ValueError(
                    f"invalid Hermes-link state transition: {latest.state.value} -> {target.value}"
                )
            if len(records) >= self.capacity:
                raise OverflowError("Hermes-link journal capacity reached")
            record = LinkJournalRecord.build(
                sequence=len(records) + 1,
                envelope=updated,
                state=target,
                recorded_at=recorded_at or utc_now(),
                reason_code=reason_code,
                previous_checksum=records[-1].checksum if records else None,
            )
            payload = (record.model_dump_json() + "\n").encode()
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("Hermes-link journal write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        return updated

    def get(self, message_id: str) -> Optional[HermesLinkEnvelope]:
        record = self._latest(self.records()).get(message_id)
        return None if record is None else record.envelope

    def list(
        self, *, state: Optional[DeliveryState] = None
    ) -> Tuple[HermesLinkEnvelope, ...]:
        values = [record.envelope for record in self._latest(self.records()).values()]
        if state is not None:
            values = [item for item in values if item.delivery_state == state]
        return tuple(
            sorted(values, key=lambda item: (item.created_at, item.message_id))
        )

    def records(self) -> Tuple[LinkJournalRecord, ...]:
        with self._lock():
            return self._read_unlocked(recover_torn_tail=True)

    @staticmethod
    def _latest(records: Tuple[LinkJournalRecord, ...]) -> dict[str, LinkJournalRecord]:
        return {record.envelope.message_id: record for record in records}

    def _read_unlocked(
        self, *, recover_torn_tail: bool
    ) -> Tuple[LinkJournalRecord, ...]:
        if not self.path.exists():
            return ()
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if recover_torn_tail:
                fd = os.open(self.path, os.O_WRONLY)
                try:
                    os.ftruncate(fd, boundary)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            raw = raw[:boundary]
        records = []
        previous = None
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                record = LinkJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(f"corrupt Hermes-link journal line {number}") from exc
            if record.sequence != number or record.previous_checksum != previous:
                raise ValueError("Hermes-link journal chain mismatch")
            records.append(record)
            previous = record.checksum
        return tuple(records)
