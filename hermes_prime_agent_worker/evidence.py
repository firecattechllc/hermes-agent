"""Append-only, hash-chained evidence store for the Prime Agent worker.

A self-contained package-local store (like ``hermes_docs_worker.evidence``),
not the fleet-wide ``hermes_cli.prime.evidence.PrimeEvidenceStore`` -- Prime
Agent is a Titan-local bounded worker, not a fleet admission participant, so
it does not need fleet identity chaining. It borrows that store's core
guarantee though: every append is hash-chained to the previous entry and
fsync'd, so tampering with a historical line is detectable, not just
appending new ones is possible. Retention is bounded by both age and count
so this never grows unbounded on a Pi 5's disk.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from hermes_prime_agent_worker.redaction import redact_text

_GENESIS_HASH = "0" * 64
_LEDGER_FILENAME = "evidence.jsonl"
_LOCK_FILENAME = "evidence.lock"


class EvidenceStorageError(RuntimeError):
    """The evidence ledger is missing, corrupt, or its hash chain does not
    verify. Raised rather than silently trusting unverifiable evidence."""


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    category: str
    action: str
    status: str
    reason_codes: Tuple[str, ...]
    detail: str
    correlation_id: str
    timestamp: int

    @classmethod
    def build(
        cls,
        *,
        category: str,
        action: str,
        status: str,
        reason_codes: Tuple[str, ...] = (),
        detail: str = "",
        correlation_id: str,
        now: Optional[int] = None,
    ) -> "EvidenceRecord":
        return cls(
            category=category,
            action=action,
            status=status,
            reason_codes=tuple(reason_codes),
            detail=redact_text(detail)[:4000],
            correlation_id=correlation_id,
            timestamp=now if now is not None else int(time.time()),
        )

    def to_dict(self) -> Mapping[str, Any]:
        return asdict(self)


def _entry_hash(previous_hash: str, sequence: int, record: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"sequence": sequence, "previous_record_hash": previous_hash, "record": record},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EvidenceStore:
    """Bounded, ``flock``-protected, hash-chained JSONL append log."""

    def __init__(
        self,
        root: Path,
        *,
        retention_days: int = 30,
        max_files: int = 500,
    ) -> None:
        self._root = root
        self._retention_days = retention_days
        self._max_files = max_files
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ledger_path = self._root / _LEDGER_FILENAME
        self._lock_path = self._root / _LOCK_FILENAME

    def append(self, record: EvidenceRecord) -> Mapping[str, Any]:
        with open(self._lock_path, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                sequence, previous_hash = self._tail_state()
                sequence += 1
                record_dict = record.to_dict()
                entry_hash = _entry_hash(previous_hash, sequence, record_dict)
                entry = {
                    "sequence": sequence,
                    "previous_record_hash": previous_hash,
                    "record": record_dict,
                    "entry_hash": entry_hash,
                }
                with open(self._ledger_path, "a", encoding="utf-8") as ledger_file:
                    ledger_file.write(json.dumps(entry, sort_keys=True) + "\n")
                    ledger_file.flush()
                    os.fsync(ledger_file.fileno())
                self._prune_locked()
                return entry
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _tail_state(self) -> Tuple[int, str]:
        if not self._ledger_path.exists():
            return 0, _GENESIS_HASH
        last_sequence = 0
        last_hash = _GENESIS_HASH
        with open(self._ledger_path, "r", encoding="utf-8") as ledger_file:
            for line in ledger_file:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                last_sequence = entry["sequence"]
                last_hash = entry["entry_hash"]
        return last_sequence, last_hash

    def _prune_locked(self) -> None:
        if not self._ledger_path.exists():
            return
        cutoff = time.time() - (self._retention_days * 86_400)
        entries = []
        with open(self._ledger_path, "r", encoding="utf-8") as ledger_file:
            for line in ledger_file:
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
        kept = [e for e in entries if e["record"]["timestamp"] >= cutoff]
        if len(kept) > self._max_files:
            kept = kept[-self._max_files :]
        if len(kept) == len(entries):
            return
        with open(self._ledger_path, "w", encoding="utf-8") as ledger_file:
            for entry in kept:
                ledger_file.write(json.dumps(entry, sort_keys=True) + "\n")
            ledger_file.flush()
            os.fsync(ledger_file.fileno())

    def read_all(self) -> Tuple[Mapping[str, Any], ...]:
        if not self._ledger_path.exists():
            return ()
        entries = []
        with open(self._ledger_path, "r", encoding="utf-8") as ledger_file:
            for line in ledger_file:
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
        return tuple(entries)

    def verify_chain(self) -> bool:
        """Recomputes every entry's hash against its stored value and
        checks the previous-hash linkage. Returns False (never raises) on
        the first mismatch found -- pruning legitimately truncates the
        chain's head, so a fresh genesis after pruning is expected and this
        only verifies internal consistency of what remains."""
        entries = self.read_all()
        previous_hash = entries[0]["previous_record_hash"] if entries else _GENESIS_HASH
        for entry in entries:
            expected = _entry_hash(previous_hash, entry["sequence"], entry["record"])
            if expected != entry["entry_hash"]:
                return False
            previous_hash = entry["entry_hash"]
        return True
