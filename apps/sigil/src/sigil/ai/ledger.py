"""Durable append-only evidence ledger for governed AI activity."""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path

from .models import Capability, ExecutionLocation, validate_identifier, validate_safe_metadata
from .registry import canonical_digest
from .routing import RoutingDecision, RoutingRequest

AI_EVIDENCE_LEDGER_VERSION = 1
_ZERO_HASH = "0" * 64
_SHA256 = re.compile(r"^(?:invalid:)?sha256:[0-9a-f]{64}$")
_EVIDENCE_IDENTITY = re.compile(r"^(?:sha256:|ai-evidence-)[0-9a-f]{64}$")
_RECORD_KEYS = frozenset(
    {
        "broker_submission",
        "capability",
        "ended_at",
        "entry_hash",
        "evidence_identity",
        "execution_location",
        "failure_classification",
        "fallback",
        "input_digest",
        "ledger_sequence",
        "ledger_version",
        "model_id",
        "model_version",
        "output_digest",
        "paper_only",
        "previous_record_hash",
        "provider_id",
        "provider_metadata",
        "record_type",
        "registry_revision",
        "request_id",
        "routing_status",
        "started_at",
        "succeeded",
        "task_correlation_id",
    }
)


class AIEvidenceLedgerError(RuntimeError):
    """Base error for governed AI evidence persistence."""


class AIEvidenceCorruptionError(AIEvidenceLedgerError):
    """Persisted AI evidence failed structural or integrity validation."""


class AIEvidenceConflictError(AIEvidenceLedgerError):
    """An evidence identity is already committed."""


class AIEvidenceRecordType(str, Enum):
    ROUTING_DECISION = "routing_decision"
    FALLBACK_DECISION = "fallback_decision"
    PROVIDER_INVOCATION_ATTEMPT = "provider_invocation_attempt"
    PROVIDER_RESULT_SUCCEEDED = "provider_result_succeeded"
    PROVIDER_RESULT_FAILED = "provider_result_failed"
    PROVIDER_HEALTH_REJECTED = "provider_health_rejected"


@dataclass(frozen=True, slots=True)
class GovernedAIEvidenceRecord:
    evidence_identity: str
    record_type: AIEvidenceRecordType
    request_id: str
    task_correlation_id: str
    provider_id: str | None
    model_id: str | None
    model_version: str | None
    registry_revision: str
    capability: Capability
    execution_location: ExecutionLocation | None
    routing_status: str
    fallback: bool
    started_at: str
    ended_at: str
    succeeded: bool
    failure_classification: str | None
    input_digest: str
    output_digest: str | None
    provider_metadata: tuple[tuple[str, str], ...] = ()
    ledger_version: int = AI_EVIDENCE_LEDGER_VERSION
    ledger_sequence: int = 0
    previous_record_hash: str = _ZERO_HASH
    entry_hash: str = ""
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if self.ledger_version != AI_EVIDENCE_LEDGER_VERSION:
            raise AIEvidenceCorruptionError("unsupported AI evidence schema version")
        if _EVIDENCE_IDENTITY.fullmatch(self.evidence_identity) is None:
            raise AIEvidenceCorruptionError("evidence identity must be canonical SHA-256")
        for field, value in (
            ("request_id", self.request_id),
            ("task_correlation_id", self.task_correlation_id),
        ):
            validate_identifier(value, field)
        for field, value in (("provider_id", self.provider_id), ("model_id", self.model_id)):
            if value is not None:
                validate_identifier(value, field)
        if self.model_version is not None:
            validate_identifier(self.model_version, "model_version")
        if _SHA256.fullmatch(self.registry_revision) is None:
            raise AIEvidenceCorruptionError("registry revision must be a SHA-256 identity")
        if _SHA256.fullmatch(self.input_digest) is None or self.input_digest.startswith("invalid:"):
            raise AIEvidenceCorruptionError("input digest must be a SHA-256 identity")
        if self.output_digest is not None and (
            _SHA256.fullmatch(self.output_digest) is None
            or self.output_digest.startswith("invalid:")
        ):
            raise AIEvidenceCorruptionError("output digest must be a SHA-256 identity")
        if not self.started_at or not self.ended_at:
            raise AIEvidenceCorruptionError("AI evidence timestamps cannot be blank")
        if self.paper_only is not True or self.broker_submission is not False:
            raise AIEvidenceCorruptionError("AI evidence must remain paper-only")
        validate_safe_metadata(self.provider_metadata, "provider metadata")


def evidence_identity(payload: Mapping[str, object]) -> str:
    """Create an immutable identity without retaining source content."""
    return f"ai-evidence-{canonical_digest(payload)}"


def _record_payload(record: GovernedAIEvidenceRecord, *, include_hash: bool) -> dict[str, object]:
    payload = asdict(record)
    payload["record_type"] = record.record_type.value
    payload["capability"] = record.capability.value
    payload["execution_location"] = (
        None if record.execution_location is None else record.execution_location.value
    )
    payload["provider_metadata"] = [list(item) for item in record.provider_metadata]
    if not include_hash:
        payload.pop("entry_hash")
    return payload


class DurableAIEvidenceLedger:
    """Hash-chained, append-only local AI evidence with torn-tail recovery."""

    def __init__(self, state_root: Path) -> None:
        if not isinstance(state_root, Path) or not state_root.is_absolute():
            raise AIEvidenceLedgerError("AI evidence state root must be an absolute Path")
        if state_root.is_symlink() or not state_root.exists() or not state_root.is_dir():
            raise AIEvidenceLedgerError(
                "AI evidence state root must be an existing non-symlink directory"
            )
        self.directory = state_root / "governed-ai-evidence-v1"
        self.path = self.directory / "ledger.jsonl"
        self.lock_path = self.directory / "ledger.lock"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise AIEvidenceLedgerError("AI evidence paths cannot use symlinks")
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

    def read_records(
        self, *, recover_truncated_tail: bool = True
    ) -> tuple[GovernedAIEvidenceRecord, ...]:
        with self._locked():
            return self._read_unlocked(recover_truncated_tail=recover_truncated_tail)

    def append(self, record: GovernedAIEvidenceRecord) -> GovernedAIEvidenceRecord:
        if record.ledger_sequence != 0 or record.entry_hash:
            raise AIEvidenceLedgerError("new AI evidence cannot predeclare ledger linkage")
        with self._locked():
            records = self._read_unlocked(recover_truncated_tail=True)
            if any(item.evidence_identity == record.evidence_identity for item in records):
                raise AIEvidenceConflictError("duplicate AI evidence identity")
            sequence = len(records) + 1
            previous = records[-1].entry_hash if records else _ZERO_HASH
            linked = replace(record, ledger_sequence=sequence, previous_record_hash=previous)
            committed = replace(
                linked,
                entry_hash=canonical_digest(_record_payload(linked, include_hash=False)),
            )
            encoded = (
                json.dumps(
                    _record_payload(committed, include_hash=True),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
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
                        raise AIEvidenceLedgerError("AI evidence write made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory()
            return committed

    def _read_unlocked(
        self, *, recover_truncated_tail: bool
    ) -> tuple[GovernedAIEvidenceRecord, ...]:
        if not self.path.exists():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise AIEvidenceCorruptionError("AI evidence ledger path is unsafe")
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if not recover_truncated_tail:
                raise AIEvidenceCorruptionError("AI evidence ledger has a truncated tail")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.ftruncate(descriptor, boundary)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory()
            raw = raw[:boundary]

        records: list[GovernedAIEvidenceRecord] = []
        identities: set[str] = set()
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                payload = json.loads(line)
                record = self._decode(payload)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise AIEvidenceCorruptionError(
                    f"corrupt AI evidence ledger line {number}"
                ) from error
            expected_previous = records[-1].entry_hash if records else _ZERO_HASH
            if record.ledger_sequence != number:
                raise AIEvidenceCorruptionError("AI evidence ledger sequence mismatch")
            if record.previous_record_hash != expected_previous:
                raise AIEvidenceCorruptionError("AI evidence ledger hash chain mismatch")
            expected_hash = canonical_digest(_record_payload(record, include_hash=False))
            if record.entry_hash != expected_hash:
                raise AIEvidenceCorruptionError("AI evidence record hash mismatch")
            if record.evidence_identity in identities:
                raise AIEvidenceCorruptionError("duplicate AI evidence identity")
            identities.add(record.evidence_identity)
            records.append(record)
        return tuple(records)

    @staticmethod
    def _decode(payload: object) -> GovernedAIEvidenceRecord:
        if not isinstance(payload, dict) or frozenset(payload) != _RECORD_KEYS:
            raise AIEvidenceCorruptionError("AI evidence record shape is invalid")
        metadata = payload["provider_metadata"]
        if not isinstance(metadata, list):
            raise AIEvidenceCorruptionError("AI evidence metadata is invalid")
        return GovernedAIEvidenceRecord(
            **{
                **payload,
                "record_type": AIEvidenceRecordType(payload["record_type"]),
                "capability": Capability(payload["capability"]),
                "execution_location": (
                    None
                    if payload["execution_location"] is None
                    else ExecutionLocation(payload["execution_location"])
                ),
                "provider_metadata": tuple(tuple(item) for item in metadata),
            }
        )

    def _fsync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def append_routing_decision(
    ledger: DurableAIEvidenceLedger,
    *,
    request: RoutingRequest,
    decision: RoutingDecision,
    model_version: str | None,
    execution_location: ExecutionLocation | None,
) -> GovernedAIEvidenceRecord:
    """Persist a sanitized routing or fallback decision."""
    capability = min(request.required_capabilities, key=lambda item: item.value)
    return ledger.append(
        GovernedAIEvidenceRecord(
            evidence_identity=decision.evidence_identity,
            record_type=(
                AIEvidenceRecordType.FALLBACK_DECISION
                if decision.fallback
                else AIEvidenceRecordType.ROUTING_DECISION
            ),
            request_id=request.request_id,
            task_correlation_id=request.task_correlation_id,
            provider_id=decision.selected_provider_id,
            model_id=decision.selected_model_id,
            model_version=model_version,
            registry_revision=decision.registry_revision,
            capability=capability,
            execution_location=execution_location,
            routing_status="selected" if decision.succeeded else "rejected",
            fallback=decision.fallback,
            started_at=decision.decision_timestamp,
            ended_at=decision.decision_timestamp,
            succeeded=decision.succeeded,
            failure_classification=(
                None if decision.failure_class is None else decision.failure_class.value
            ),
            input_digest=f"sha256:{canonical_digest(asdict(request))}",
            output_digest=None,
        )
    )
