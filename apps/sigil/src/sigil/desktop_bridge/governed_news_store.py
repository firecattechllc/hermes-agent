"""Durable, hash-chained storage for governed news evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .governed_news import build_news_intelligence, normalize_news_item

ZERO_SHA256 = "0" * 64
LEDGER_NAME = "governed-news-evidence.jsonl"


class NewsEvidenceStore:
    """Append-only local evidence ledger with deterministic deduplication."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / LEDGER_NAME

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def _digest(cls, value: object) -> str:
        import hashlib

        return hashlib.sha256(cls._canonical(value)).hexdigest()

    @classmethod
    def _duplicate_identity(cls, record: dict[str, Any]) -> str:
        """Identify the same article independently of ingestion time."""
        stable_fields = {
            "headline": record.get("headline"),
            "summary": record.get("summary"),
            "source": record.get("source"),
            "source_url": record.get("source_url"),
            "published_at": record.get("published_at"),
            "symbols": record.get("symbols"),
            "sentiment": record.get("sentiment"),
            "confidence": record.get("confidence"),
        }
        return cls._digest(stable_fields)

    def _read_envelopes(self) -> list[dict[str, Any]]:
        if self.path.is_symlink():
            raise RuntimeError("governed news ledger cannot be a symlink")
        if not self.path.exists():
            return []

        previous = ZERO_SHA256
        envelopes: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line:
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"governed news ledger contains invalid JSON at line {line_number}"
                ) from error
            body = {
                "record": envelope.get("record"),
                "previous_record_sha256": envelope.get("previous_record_sha256"),
            }
            if envelope.get("previous_record_sha256") != previous:
                raise RuntimeError("governed news ledger hash chain is broken")
            expected = self._digest(body)
            if envelope.get("sha256") != expected:
                raise RuntimeError("governed news ledger integrity validation failed")
            record = envelope.get("record")
            if not isinstance(record, dict):
                raise TypeError("governed news ledger record is invalid")
            previous = expected
            envelopes.append(envelope)
        return envelopes

    def records(self) -> list[dict[str, Any]]:
        return [dict(envelope["record"]) for envelope in self._read_envelopes()]

    def ingest(self, payload: object, *, received_at: str) -> dict[str, Any]:
        record = normalize_news_item(payload, received_at=received_at)
        envelopes = self._read_envelopes()
        duplicate_identity = self._duplicate_identity(record)
        existing_duplicate_identities = {
            self._duplicate_identity(envelope["record"]) for envelope in envelopes
        }
        if duplicate_identity in existing_duplicate_identities:
            return {
                "status": "duplicate",
                "record": record,
                "duplicate_identity": duplicate_identity,
                "broker_submission_attempted": False,
            }

        previous = envelopes[-1]["sha256"] if envelopes else ZERO_SHA256
        body = {
            "record": record,
            "previous_record_sha256": previous,
        }
        envelope = {**body, "sha256": self._digest(body)}

        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RuntimeError("governed news directory cannot be a symlink")
        with self.path.open("a", encoding="utf-8") as output:
            os.chmod(self.path, 0o600)
            output.write(self._canonical(envelope).decode())
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())

        return {
            "status": "stored",
            "record": record,
            "ledger_sha256": envelope["sha256"],
            "broker_submission_attempted": False,
        }

    def projection(self) -> dict[str, Any]:
        return build_news_intelligence(self.records())

    def symbol_timeline(self, symbol: object) -> dict[str, Any]:
        normalized = str(symbol).strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        items = [record for record in self.records() if normalized in record.get("symbols", [])]
        items.sort(
            key=lambda item: (item["published_at"], item["evidence_identity"]),
            reverse=True,
        )
        return {
            "symbol": normalized,
            "headline_count": len(items),
            "headlines": items,
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }
