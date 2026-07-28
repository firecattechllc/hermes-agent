"""Checksummed, atomic persistence for governed universe snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import (
    CanonicalInstrument,
    SourceEvidence,
    UniverseSnapshot,
    UniverseValidationError,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class UniverseStore:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise UniverseValidationError("universe store path must be absolute")
        self.path = path

    def write(self, snapshot: UniverseSnapshot) -> None:
        payload = snapshot.to_dict()
        envelope = {
            "payload": payload,
            "checksum": hashlib.sha256(_canonical(payload)).hexdigest(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("wb") as handle:
            handle.write(_canonical(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def read(self) -> UniverseSnapshot:
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            checksum = envelope["checksum"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            raise UniverseValidationError("universe store is unreadable") from None
        if not isinstance(checksum, str) or hashlib.sha256(_canonical(payload)).hexdigest() != checksum:
            raise UniverseValidationError("universe store checksum mismatch")
        try:
            instruments = tuple(
                CanonicalInstrument(
                    **{
                        **item,
                        "aliases": tuple(item["aliases"]),
                        "exclusion_reasons": tuple(item["exclusion_reasons"]),
                        "conflict_fields": tuple(item["conflict_fields"]),
                        "evidence": tuple(SourceEvidence(**evidence) for evidence in item["evidence"]),
                    }
                )
                for item in payload["instruments"]
            )
            snapshot = UniverseSnapshot(
                schema_version=payload["schema_version"],
                policy_version=payload["policy_version"],
                generated_at=payload["generated_at"],
                source_record_count=payload["source_record_count"],
                instruments=instruments,
                snapshot_id=payload["snapshot_id"],
            )
        except (KeyError, TypeError, ValueError):
            raise UniverseValidationError("universe store schema is invalid") from None
        if _canonical(snapshot.to_dict()) != _canonical(payload):
            raise UniverseValidationError("universe store canonical form is invalid")
        return snapshot
