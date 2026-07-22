"""Checksummed local JSONL storage for Step 29 certification artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generic, Iterator, Optional, Tuple, TypeVar

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .release_readiness import ReleaseReadiness
from .system_integration_certification import SystemIntegrationCertification


SYSTEM_INTEGRATION_CERTIFICATION_JOURNAL = "system-integration-certifications.jsonl"
RELEASE_READINESS_JOURNAL = "release-readiness.jsonl"
Artifact = TypeVar("Artifact", SystemIntegrationCertification, ReleaseReadiness)


class CertificationJournalRecord(BaseModel, Generic[Artifact]):
    model_config = ConfigDict(frozen=True, extra="forbid")
    journal_sequence: int = Field(..., ge=1)
    artifact_id: str
    idempotency_key: str
    artifact: Artifact
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(sequence: int, artifact_id: str, idempotency_key: str, artifact: BaseModel) -> str:
        payload = {"journal_sequence": sequence, "artifact_id": artifact_id, "idempotency_key": idempotency_key, "artifact": artifact.model_dump(mode="json")}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @model_validator(mode="after")
    def _consistent(self) -> "CertificationJournalRecord[Artifact]":
        if self.checksum != self.calculate_checksum(self.journal_sequence, self.artifact_id, self.idempotency_key, self.artifact):
            raise ValueError("Step 29 certification journal checksum mismatch")
        return self


class _ArtifactStore(Generic[Artifact]):
    def __init__(self, root: Path, filename: str, adapter: TypeAdapter, id_field: str, *, capacity: int = 10_000) -> None:
        if not 1 <= capacity <= 100_000:
            raise ValueError("Step 29 store capacity must be between 1 and 100000")
        self.root = Path(root); self.filename = filename; self.adapter = adapter; self.id_field = id_field; self.capacity = capacity
        self._thread_lock = threading.RLock()

    @property
    def journal_path(self) -> Path:
        return self.root / self.filename

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True); os.chmod(self.root, 0o700)
        lock_path = self.journal_path.with_suffix(".lock")
        with self._thread_lock, lock_path.open("a+", encoding="utf-8") as handle:
            os.chmod(lock_path, 0o600)
            if fcntl is not None: fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try: yield
            finally:
                if fcntl is not None: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> Tuple[CertificationJournalRecord[Artifact], ...]:
        if not self.journal_path.exists(): return ()
        raw = self.journal_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("truncated Step 29 certification journal")
        records = []
        for index, line in enumerate(raw.splitlines(), 1):
            try:
                payload = json.loads(line)
                artifact = self.adapter.validate_python(payload.pop("artifact"))
                record = CertificationJournalRecord(**payload, artifact=artifact)
            except Exception as exc:
                raise ValueError(f"invalid Step 29 journal record at line {index}: {exc}") from exc
            if record.journal_sequence != index:
                raise ValueError("Step 29 journal sequence mismatch")
            records.append(record)
        return tuple(records)

    def list(self) -> Tuple[Artifact, ...]:
        with self._lock():
            return tuple(record.artifact for record in self._read())

    def get(self, artifact_id: str) -> Optional[Artifact]:
        return next((item for item in self.list() if getattr(item, self.id_field) == artifact_id), None)

    def save(self, artifact: Artifact, *, idempotency_key: str) -> Artifact:
        if not idempotency_key.strip() or len(idempotency_key) > 256:
            raise ValueError("Step 29 idempotency key must be non-empty and bounded")
        artifact_id = getattr(artifact, self.id_field)
        with self._lock():
            records = self._read()
            current = next((item for item in records if item.artifact_id == artifact_id or item.idempotency_key == idempotency_key), None)
            if current is not None:
                if current.artifact == artifact and current.artifact_id == artifact_id and current.idempotency_key == idempotency_key: return current.artifact
                raise ValueError("Step 29 certification identity or idempotency collision")
            if len(records) >= self.capacity: raise ValueError("Step 29 certification store capacity exceeded")
            sequence = len(records) + 1
            checksum = CertificationJournalRecord.calculate_checksum(sequence, artifact_id, idempotency_key, artifact)
            record = CertificationJournalRecord(journal_sequence=sequence, artifact_id=artifact_id, idempotency_key=idempotency_key, artifact=artifact, checksum=checksum)
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"); handle.flush(); os.fsync(handle.fileno())
            os.chmod(self.journal_path, 0o600)
            return artifact


class SystemIntegrationCertificationStore(_ArtifactStore[SystemIntegrationCertification]):
    def __init__(self, root: Path, *, capacity: int = 10_000) -> None:
        super().__init__(root, SYSTEM_INTEGRATION_CERTIFICATION_JOURNAL, TypeAdapter(SystemIntegrationCertification), "certification_id", capacity=capacity)


class ReleaseReadinessStore(_ArtifactStore[ReleaseReadiness]):
    def __init__(self, root: Path, *, capacity: int = 10_000) -> None:
        super().__init__(root, RELEASE_READINESS_JOURNAL, TypeAdapter(ReleaseReadiness), "report_id", capacity=capacity)
