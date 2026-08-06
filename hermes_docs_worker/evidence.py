"""Evidence facts, normalization, and retention.

An :class:`EvidenceFact` is the atomic unit every collector produces and
every Markdown generator consumes. It is deliberately small and flat (no
nested free-form blobs) so redaction and contradiction detection can reason
about it without parsing arbitrary structure, and it cannot be constructed
at all if its text still contains secret material (see
:func:`redaction.assert_redacted`) -- redaction happens at the collector
boundary, not as an optional cleanup step later.

This module intentionally does not reuse
:class:`hermes_cli.prime.evidence.PrimeEvidenceStore`: that store is the
identity-linked, hash-chained journal for Prime's Stage 2 control-plane
events (registration, admission, health, certification). Titan
documentation evidence is a different domain -- disk-budget-conscious,
locally retained and pruned, not linked to a Prime identity chain -- so it
gets its own lightweight, flock-protected JSONL store rather than being
force-fit into that schema.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hermes_docs_worker.redaction import assert_redacted, contains_secret, redact_text
from hermes_docs_worker.status import StatusValue

EVIDENCE_SCHEMA_VERSION = 1


class EvidenceFact(BaseModel):
    """One normalized, redacted, governed-status-tagged observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = EVIDENCE_SCHEMA_VERSION
    category: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=128)
    status: StatusValue
    detail: str = Field(default="", max_length=1024)
    source: str = Field(..., min_length=1, max_length=128)
    collected_at: int = Field(..., ge=0)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported evidence schema version")
        return v

    @model_validator(mode="after")
    def _redacted(self) -> "EvidenceFact":
        assert_redacted(self.detail, field_name="detail")
        assert_redacted(self.label, field_name="label")
        return self

    def key(self) -> Tuple[str, str]:
        """Identity used to group facts about "the same subject" for
        contradiction detection and status-collapsing."""
        return (self.category, self.label)


class EvidenceSnapshot(BaseModel):
    """Everything one collection run observed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = EVIDENCE_SCHEMA_VERSION
    run_id: str = Field(..., min_length=1, max_length=64)
    collected_at: int = Field(..., ge=0)
    facts: Tuple[EvidenceFact, ...] = ()
    collector_errors: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _redacted(self) -> "EvidenceSnapshot":
        for error in self.collector_errors:
            assert_redacted(error, field_name="collector_errors")
        return self

    def facts_by_key(self) -> dict[Tuple[str, str], list[EvidenceFact]]:
        grouped: dict[Tuple[str, str], list[EvidenceFact]] = {}
        for fact in self.facts:
            grouped.setdefault(fact.key(), []).append(fact)
        return grouped


def make_fact(
    *, category: str, label: str, status: StatusValue, detail: str, source: str,
    collected_at: int,
) -> EvidenceFact:
    """The collector-facing constructor: redacts ``label``/``detail`` first,
    then falls back to a safe ``Unknown`` fact (never raises) if the
    redacted text still trips the fail-closed secret check -- a collector
    calling this can never crash a run by observing something that looks
    like a credential."""
    safe_label = redact_text(label)
    safe_detail = redact_text(detail)
    try:
        return EvidenceFact(
            category=category, label=safe_label, status=status, detail=safe_detail,
            source=source, collected_at=collected_at,
        )
    except ValidationError:
        fallback_label = "redacted" if contains_secret(safe_label) else safe_label
        return EvidenceFact(
            category=category, label=fallback_label, status=StatusValue.UNKNOWN,
            detail="[REDACTED — collected value still matched a secret pattern after redaction]",
            source=source, collected_at=collected_at,
        )


def now_epoch() -> int:
    return int(time.time())


def make_run_id(now: Optional[int] = None) -> str:
    ts = now if now is not None else now_epoch()
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))


class EvidenceStoreError(RuntimeError):
    """Evidence retention state failed closed."""


class EvidenceRetentionStore:
    """Append-only JSONL of past :class:`EvidenceSnapshot` runs, pruned by
    age and count on every write so a Pi 5's disk budget can't be consumed
    by an indefinitely growing history."""

    def __init__(self, state_dir: Path) -> None:
        if not state_dir.is_absolute():
            raise EvidenceStoreError("evidence state_dir must be an absolute path")
        self.directory = state_dir / "evidence"
        self.journal_path = self.directory / "snapshots.jsonl"
        self.lock_path = self.directory / "snapshots.lock"

    def _lock(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.lock_path.is_symlink():
            raise EvidenceStoreError("evidence directory/lock must not be a symlink")
        return self.lock_path.open("a+", encoding="utf-8")

    def append(
        self, snapshot: EvidenceSnapshot, *, retention_days: int, max_files: int
    ) -> None:
        with self._lock() as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                records = self._read_unlocked()
                records.append(snapshot)
                cutoff = now_epoch() - retention_days * 86_400
                kept = [r for r in records if r.collected_at >= cutoff]
                if len(kept) > max_files:
                    kept = kept[-max_files:]
                with self.journal_path.open("w", encoding="utf-8") as handle:
                    for record in kept:
                        handle.write(
                            json.dumps(record.model_dump(mode="json"), sort_keys=True)
                            + "\n"
                        )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def read_all(self) -> Tuple[EvidenceSnapshot, ...]:
        if not self.journal_path.exists():
            return ()
        with self._lock() as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                return tuple(self._read_unlocked())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def latest(self) -> Optional[EvidenceSnapshot]:
        records = self.read_all()
        return records[-1] if records else None

    def _read_unlocked(self) -> list[EvidenceSnapshot]:
        if not self.journal_path.exists():
            return []
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise EvidenceStoreError("evidence journal is unreadable") from error
        records: list[EvidenceSnapshot] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                records.append(EvidenceSnapshot.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError) as error:
                raise EvidenceStoreError("evidence journal entry is invalid") from error
        return records
