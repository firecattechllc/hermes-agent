"""Atomic checksummed bounded market-data state cache."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class MarketDataCache:
    def __init__(self, path: Path, *, retention: int = 10_000) -> None:
        if not path.is_absolute() or retention < 1:
            raise ValueError("cache requires an absolute path and positive retention")
        self.path, self.retention = path, retention

    def write(self, payload: dict[str, object]) -> None:
        safe = {**payload, "schema_version": 1}
        observations = safe.get("observations")
        if isinstance(observations, list):
            safe["observations"] = observations[-self.retention:]
        envelope = {"payload": safe, "checksum": hashlib.sha256(_bytes(safe)).hexdigest()}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("wb") as handle:
            handle.write(_bytes(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def read(self) -> dict[str, object]:
        try:
            envelope = json.loads(self.path.read_text())
            payload, checksum = envelope["payload"], envelope["checksum"]
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("market-data cache is unreadable") from None
        if hashlib.sha256(_bytes(payload)).hexdigest() != checksum:
            raise ValueError("market-data cache checksum mismatch")
        if payload.get("schema_version") != 1:
            raise ValueError("market-data cache schema is unsupported")
        return payload
