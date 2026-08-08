"""Unified evidence layer.

Fleet Unification Stage 2D. Provides one canonical evidence record and
append-only, hash-chained store for content produced natively by the Stage 2
control plane (Prime identity registration, health observation, admission
decisions, Sigil contract invocations, remote-maintenance governance
decisions, and fleet certification).

This module does not replace, migrate, or weaken any of the repository's
existing evidence stores:

- ``sigil.certification.evidence`` (Stage 1 certification evidence, read
  from committed markdown artifacts)
- ``sigil.worker_contract.DurableWorkerContractStore``
- ``sigil.ai.fleet.DurableFleetStore``
- ``hermes_cli.agent_roles.system_integration_certification.EvidenceReference``
  / ``EvidenceChainManifest``
- ``hermes_cli.hermes_link.security.CredentialEvidenceStore``
- the per-subsystem ``*Evidence`` models in ``hermes_cli.agent_roles``
  (``MaintenanceEvidence``, ``InventoryEvidence``, ``ModelExecutionEvidence``,
  ``IntelligenceEvidence``, ...)

Those formats already exist, are already certified against, and are linked
here via :class:`ExternalEvidenceLink` rather than being duplicated or
broken. The storage pattern used below (append-only, ``fcntl``-locked,
sequence + previous-hash + entry-hash hash chain, atomic snapshot writes)
deliberately mirrors ``sigil.worker_contract.DurableWorkerContractStore`` so
this is the *same* evidence-storage convention applied to new content, not a
sixth incompatible pattern.

Evidence proves that something occurred. It never grants authority.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVIDENCE_SCHEMA_VERSION = 1
SUPPORTED_EVIDENCE_SCHEMA_VERSIONS = frozenset({1})

_ZERO_HASH = "0" * 64


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_EVIDENCE_SCHEMA_VERSIONS:
        raise ValueError(
            f"evidence schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_EVIDENCE_SCHEMA_VERSIONS)})"
        )
    return version


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class EvidenceStorageError(RuntimeError):
    """Durable evidence state failed closed."""


class SensitivityTier(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class ExternalEvidenceSystem(str, Enum):
    """Pre-existing evidence stores this record may link to (never replace)."""

    SIGIL_CERTIFICATION = "sigil_certification"
    SIGIL_WORKER_CONTRACT = "sigil_worker_contract"
    SIGIL_AI_FLEET = "sigil_ai_fleet"
    SYSTEM_INTEGRATION_CERTIFICATION = "system_integration_certification"
    HERMES_LINK_CREDENTIAL = "hermes_link_credential"
    MISSION_CONTROL_EVENT = "mission_control_event"
    OTHER_AGENT_ROLES_EVIDENCE = "agent_roles_evidence"


class ExternalEvidenceLink(BaseModel):
    """A verifiable pointer into a pre-existing, already-certified evidence store.

    This never re-stores the linked content — only a reference and, where
    available, a content hash so tampering with the external record can be
    detected without this module needing write access to it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    system: ExternalEvidenceSystem
    reference: str = Field(..., min_length=1, max_length=1024)
    content_hash: Optional[str] = Field(default=None, min_length=64, max_length=64)


class EvidenceRecord(BaseModel):
    """A canonical, content-addressed, append-only evidence record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(..., min_length=1, max_length=160)
    schema_version: int = Field(default=EVIDENCE_SCHEMA_VERSION)
    kind: str = Field(..., min_length=1, max_length=128)
    producer_identity_id: str = Field(..., min_length=1, max_length=128)
    subject_identity_id: Optional[str] = Field(default=None, max_length=128)
    provenance: str = Field(..., min_length=1, max_length=256)
    timestamp: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)
    causation_id: Optional[str] = Field(default=None, max_length=128)
    event_refs: Tuple[str, ...] = ()
    decision_refs: Tuple[str, ...] = ()
    external_links: Tuple[ExternalEvidenceLink, ...] = ()
    sensitivity: SensitivityTier = SensitivityTier.INTERNAL
    redacted_summary: str = Field(..., max_length=2048)
    content_hash: str = Field(..., min_length=64, max_length=64)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @classmethod
    def build(
        cls,
        *,
        kind: str,
        producer_identity_id: str,
        subject_identity_id: Optional[str],
        provenance: str,
        timestamp: int,
        redacted_summary: str,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        event_refs: Tuple[str, ...] = (),
        decision_refs: Tuple[str, ...] = (),
        external_links: Tuple[ExternalEvidenceLink, ...] = (),
        sensitivity: SensitivityTier = SensitivityTier.INTERNAL,
    ) -> "EvidenceRecord":
        """Construct a record with a deterministic, content-addressed ID."""
        payload = {
            "kind": kind,
            "producer_identity_id": producer_identity_id,
            "subject_identity_id": subject_identity_id,
            "provenance": provenance,
            "timestamp": timestamp,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "event_refs": list(event_refs),
            "decision_refs": list(decision_refs),
            "external_links": [link.model_dump(mode="json") for link in external_links],
            "redacted_summary": redacted_summary,
        }
        content_hash = _digest(payload)
        evidence_id = f"pevd_{content_hash[:24]}"
        return cls(
            evidence_id=evidence_id,
            kind=kind,
            producer_identity_id=producer_identity_id,
            subject_identity_id=subject_identity_id,
            provenance=provenance,
            timestamp=timestamp,
            correlation_id=correlation_id,
            causation_id=causation_id,
            event_refs=event_refs,
            decision_refs=decision_refs,
            external_links=external_links,
            sensitivity=sensitivity,
            redacted_summary=redacted_summary,
            content_hash=content_hash,
        )


def _default_state_root() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "prime"


class PrimeEvidenceStore:
    """Append-only, hash-chained evidence journal.

    Mirrors ``sigil.worker_contract.DurableWorkerContractStore``'s
    ``append_transition``/``read_evidence`` hash-chain pattern: every record
    carries ``sequence`` and ``previous_record_hash``, and the whole chain is
    re-verified on every read so tampering with any historical entry is
    detected rather than silently trusted.
    """

    def __init__(self, state_root: Optional[Path] = None) -> None:
        root = state_root if state_root is not None else _default_state_root()
        if not root.is_absolute():
            raise EvidenceStorageError("evidence state root must be an absolute path")
        if root.is_symlink():
            raise EvidenceStorageError("evidence state root cannot be a symlink")

        self.directory = root / "prime-evidence-v1"
        self.evidence_path = self.directory / "evidence.jsonl"
        self.lock_path = self.directory / "evidence.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise EvidenceStorageError("evidence directory cannot be a symlink")
        if self.lock_path.is_symlink():
            raise EvidenceStorageError("evidence lock cannot be a symlink")

        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def append(self, record: EvidenceRecord) -> dict:
        with self._lock():
            if self.evidence_path.is_symlink():
                raise EvidenceStorageError("evidence journal cannot be a symlink")

            records = self._read_unlocked()

            entry = {
                "sequence": len(records) + 1,
                "previous_record_hash": (
                    records[-1]["entry_hash"] if records else _ZERO_HASH
                ),
                "record": record.model_dump(mode="json"),
            }
            entry["entry_hash"] = _digest(entry)

            with self.evidence_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())

            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

            return entry

    def read_all(self) -> Tuple[dict, ...]:
        if not self.directory.exists() or not self.evidence_path.exists():
            return ()
        if not self.lock_path.exists() or self.lock_path.is_symlink():
            raise EvidenceStorageError("evidence read lock is unavailable")

        with self.lock_path.open("r", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._read_unlocked()

    def _read_unlocked(self) -> Tuple[dict, ...]:
        if not self.evidence_path.exists():
            return ()
        try:
            lines = self.evidence_path.read_text(encoding="utf-8").splitlines()
            records = tuple(json.loads(line) for line in lines if line.strip())
        except (OSError, json.JSONDecodeError) as error:
            raise EvidenceStorageError("evidence journal is unreadable") from error

        previous = _ZERO_HASH
        for sequence, entry in enumerate(records, 1):
            if not isinstance(entry, dict):
                raise EvidenceStorageError("evidence journal entry shape is invalid")
            expected = _digest({k: v for k, v in entry.items() if k != "entry_hash"})
            if (
                entry.get("sequence") != sequence
                or entry.get("previous_record_hash") != previous
                or entry.get("entry_hash") != expected
            ):
                raise EvidenceStorageError(
                    "evidence journal integrity is invalid "
                    f"(sequence {sequence}); refusing to trust a tampered chain"
                )
            previous = expected

        return records

    def verify_chain(self) -> bool:
        """Return True only if the whole chain verifies; never raises for a
        healthy read. A tampered or malformed chain raises
        :class:`EvidenceStorageError` (fail closed, not fail silent)."""
        self.read_all()
        return True


def now_epoch() -> int:
    return int(time.time())
