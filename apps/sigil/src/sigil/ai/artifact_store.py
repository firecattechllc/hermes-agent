"""Durable hash-chained storage for sanitized governed analysis artifacts."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .analysis import (
    ANALYSIS_SCHEMA_VERSION,
    GenericAnalysisPayload,
    GovernedAnalysisArtifact,
)
from .models import Capability, Responsibility
from .registry import canonical_digest

_ZERO_HASH = "0" * 64


class AnalysisArtifactStoreError(RuntimeError):
    """Base error for governed analysis artifact persistence."""


class AnalysisArtifactCorruptionError(AnalysisArtifactStoreError):
    """Durable artifact history failed integrity validation."""


class AnalysisArtifactConflictError(AnalysisArtifactStoreError):
    """An artifact identity is already committed."""


class DurableAnalysisArtifactStore:
    def __init__(self, state_root: Path) -> None:
        if not isinstance(state_root, Path) or not state_root.is_absolute():
            raise AnalysisArtifactStoreError("artifact state root must be an absolute Path")
        if state_root.is_symlink() or not state_root.exists() or not state_root.is_dir():
            raise AnalysisArtifactStoreError(
                "artifact state root must be an existing non-symlink directory"
            )
        self.directory = state_root / "governed-ai-artifacts-v1"
        self.path = self.directory / "artifacts.jsonl"
        self.lock_path = self.directory / "artifacts.lock"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise AnalysisArtifactStoreError("artifact paths cannot use symlinks")
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def append(self, artifact: GovernedAnalysisArtifact) -> GovernedAnalysisArtifact:
        with self._locked():
            records = self._read_unlocked(recover_truncated_tail=True)
            if any(item.artifact_id == artifact.artifact_id for item in records):
                raise AnalysisArtifactConflictError("duplicate analysis artifact identity")
            previous = self._last_entry_hash() if records else _ZERO_HASH
            envelope = {
                "artifact": self._artifact_payload(artifact),
                "entry_hash": "",
                "previous_entry_hash": previous,
                "sequence": len(records) + 1,
                "store_version": ANALYSIS_SCHEMA_VERSION,
            }
            envelope["entry_hash"] = canonical_digest(
                {key: value for key, value in envelope.items() if key != "entry_hash"}
            )
            encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            try:
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise AnalysisArtifactStoreError("analysis artifact write made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory()
            return artifact

    def read_artifacts(
        self, *, recover_truncated_tail: bool = True
    ) -> tuple[GovernedAnalysisArtifact, ...]:
        with self._locked():
            return self._read_unlocked(recover_truncated_tail=recover_truncated_tail)

    def _read_unlocked(
        self, *, recover_truncated_tail: bool
    ) -> tuple[GovernedAnalysisArtifact, ...]:
        if not self.path.exists():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise AnalysisArtifactCorruptionError("artifact store path is unsafe")
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if not recover_truncated_tail:
                raise AnalysisArtifactCorruptionError("artifact store has a truncated tail")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.ftruncate(descriptor, boundary)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory()
            raw = raw[:boundary]
        artifacts: list[GovernedAnalysisArtifact] = []
        identities: set[str] = set()
        previous = _ZERO_HASH
        self._validated_last_hash = _ZERO_HASH
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                envelope = json.loads(line)
                if set(envelope) != {
                    "artifact",
                    "entry_hash",
                    "previous_entry_hash",
                    "sequence",
                    "store_version",
                }:
                    raise AnalysisArtifactCorruptionError("artifact envelope shape is invalid")
                if envelope["store_version"] != ANALYSIS_SCHEMA_VERSION:
                    raise AnalysisArtifactCorruptionError("unsupported artifact store schema")
                if envelope["sequence"] != number:
                    raise AnalysisArtifactCorruptionError("artifact sequence mismatch")
                if envelope["previous_entry_hash"] != previous:
                    raise AnalysisArtifactCorruptionError("artifact hash chain mismatch")
                expected = canonical_digest(
                    {key: value for key, value in envelope.items() if key != "entry_hash"}
                )
                if envelope["entry_hash"] != expected:
                    raise AnalysisArtifactCorruptionError("artifact entry hash mismatch")
                artifact = self._decode_artifact(envelope["artifact"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise AnalysisArtifactCorruptionError(
                    f"corrupt analysis artifact line {number}"
                ) from error
            if artifact.artifact_id in identities:
                raise AnalysisArtifactCorruptionError("duplicate analysis artifact identity")
            identities.add(artifact.artifact_id)
            artifacts.append(artifact)
            previous = envelope["entry_hash"]
            self._validated_last_hash = previous
        return tuple(artifacts)

    def _last_entry_hash(self) -> str:
        return getattr(self, "_validated_last_hash", _ZERO_HASH)

    @staticmethod
    def _artifact_payload(artifact: GovernedAnalysisArtifact) -> dict[str, object]:
        payload = asdict(artifact)
        payload["capability"] = artifact.capability.value
        payload["responsibility"] = artifact.responsibility.value
        return payload

    @staticmethod
    def _decode_artifact(payload: object) -> GovernedAnalysisArtifact:
        if not isinstance(payload, dict) or not isinstance(payload.get("structured_payload"), dict):
            raise AnalysisArtifactCorruptionError("artifact payload shape is invalid")
        structured = payload["structured_payload"]
        return GovernedAnalysisArtifact(
            **{
                **payload,
                "capability": Capability(payload["capability"]),
                "responsibility": Responsibility(payload["responsibility"]),
                "structured_payload": GenericAnalysisPayload(
                    summary=structured["summary"],
                    findings=tuple(structured["findings"]),
                    risks=tuple(structured["risks"]),
                    evidence_references=tuple(structured["evidence_references"]),
                    limitations=tuple(structured["limitations"]),
                    confidence=structured["confidence"],
                ),
                "citations": tuple(payload["citations"]),
                "limitations": tuple(payload["limitations"]),
            }
        )

    def _fsync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
