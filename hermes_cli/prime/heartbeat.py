"""Live fleet heartbeat ingestion and health-state tracking.

Fleet Unification live-runtime work. ``hermes_cli.prime.health`` already
defines the pure health protocol (``HealthReport``, ``evaluate_health``,
``is_usable_for_admission``) but nothing in Stage 2 ever ingested a real
heartbeat or persisted the latest observation for a node. This module is
that ingestion path: it turns a periodic heartbeat submission from a
registered :mod:`hermes_cli.prime.fleet_registry` node into a durable
``HealthReport`` and a derived :class:`~hermes_cli.prime.fleet_registry.FleetNodeConnectionState`.

Two invariants matter more than anything else here:

1. **Nothing is ever "healthy" just because it was healthy once.**
   :meth:`HeartbeatService.current_connection_state` always re-derives the
   connection state from the stored ``HealthReport`` against the caller's
   ``now`` — it never trusts ``FleetNodeRecord.connection_state`` at rest.
   A node that stops heartbeating therefore ages into ``STALE`` on its own,
   without needing a new event to push it there.
2. **Time is always caller-supplied.** Every method takes ``now`` explicitly
   (never reads the wall clock itself), so tests can move time forward
   deterministically and a real service can inject its own clock source.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only lock, matches evidence.py
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.prime.fleet_registry import (
    FleetNodeConnectionState,
    FleetNodeRegistry,
    FleetRegistryError,
)
from hermes_cli.prime.health import (
    DEFAULT_MAX_REPORT_AGE_SECONDS,
    DegradationLevel,
    DependencyHealth,
    HealthCheck,
    HealthFinding,
    HealthReport,
    LivenessState,
    QuarantineState,
    ReadinessState,
    evaluate_health,
)

HEARTBEAT_SCHEMA_VERSION = 1
SUPPORTED_HEARTBEAT_SCHEMA_VERSIONS = frozenset({1})

# A heartbeat report is considered valid for this long past its observation
# time even if the sender does not send another one — after this, evaluate_health
# marks it EXPIRED (not merely STALE) and the node ages to DISCONNECTED.
DEFAULT_HEARTBEAT_EXPIRY_SECONDS = 600


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_HEARTBEAT_SCHEMA_VERSIONS:
        raise ValueError(
            f"heartbeat schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_HEARTBEAT_SCHEMA_VERSIONS)})"
        )
    return version


class HeartbeatSubmission(BaseModel):
    """A single heartbeat sent by a fleet node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=HEARTBEAT_SCHEMA_VERSION)
    natural_key: str = Field(..., min_length=1, max_length=64)
    liveness: LivenessState
    readiness: ReadinessState
    dependency_health: Dict[str, DependencyHealth] = Field(default_factory=dict)
    degradation: DegradationLevel = DegradationLevel.NONE
    quarantine: QuarantineState = QuarantineState.NOT_QUARANTINED
    checks: Tuple[HealthCheck, ...] = ()
    reported_model_inventory: Tuple[str, ...] = ()
    submitted_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    def _normalize_key(self) -> str:
        return self.natural_key.strip().lower()


class HeartbeatOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class HeartbeatRejectionCode(str, Enum):
    UNKNOWN_NODE = "unknown_node"
    NODE_REVOKED = "node_revoked"
    SUBMITTED_IN_FUTURE = "submitted_in_future"


class HeartbeatResult(BaseModel):
    """The outcome of ingesting one heartbeat submission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: HeartbeatOutcome
    rejection_code: Optional[HeartbeatRejectionCode] = None
    natural_key: str = Field(..., min_length=1, max_length=64)
    connection_state: Optional[FleetNodeConnectionState] = None
    previous_connection_state: Optional[FleetNodeConnectionState] = None
    findings: Tuple[HealthFinding, ...] = ()
    health_report_id: Optional[str] = Field(default=None, max_length=160)
    decided_at: int = Field(..., ge=0)

    @property
    def transitioned(self) -> bool:
        return (
            self.outcome == HeartbeatOutcome.ACCEPTED
            and self.connection_state != self.previous_connection_state
        )


def _default_state_root() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "prime"


class HealthReportStore:
    """Durable, atomically-written, keyed store for the latest :class:`HealthReport` per node.

    Mirrors :class:`hermes_cli.prime.fleet_registry.FleetRegistryStore`'s
    single-JSON-document-with-atomic-rewrite pattern: only the *latest*
    report per node is retained (older reports are superseded, not
    accumulated), because staleness is evaluated relative to "the last thing
    we heard", not a full history.
    """

    def __init__(self, state_root: Optional[Path] = None) -> None:
        root = state_root if state_root is not None else _default_state_root()
        if not root.is_absolute():
            raise FleetRegistryError("health store state root must be an absolute path")
        if root.is_symlink():
            raise FleetRegistryError("health store state root cannot be a symlink")

        self.directory = root / "heartbeat-v1"
        self.records_path = self.directory / "latest_health.json"
        self.lock_path = self.directory / "latest_health.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise FleetRegistryError("health store directory cannot be a symlink")
        if self.lock_path.is_symlink():
            raise FleetRegistryError("health store lock cannot be a symlink")

        with self.lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _read_unlocked(self) -> Dict[str, HealthReport]:
        if not self.records_path.exists():
            return {}
        if self.records_path.is_symlink():
            raise FleetRegistryError("health store records file cannot be a symlink")
        try:
            raw = json.loads(self.records_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FleetRegistryError("health store records file is unreadable") from error
        if not isinstance(raw, dict):
            raise FleetRegistryError("health store records file shape is invalid")
        try:
            return {key: HealthReport(**value) for key, value in raw.items()}
        except Exception as error:  # noqa: BLE001 - fail closed on any malformed record
            raise FleetRegistryError("health store records file contains an invalid record") from error

    def _write_unlocked(self, records: Dict[str, HealthReport]) -> None:
        payload = {key: record.model_dump(mode="json") for key, record in records.items()}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.directory), prefix=".latest_health.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.records_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def get(self, natural_key: str) -> Optional[HealthReport]:
        with self._lock():
            return self._read_unlocked().get(natural_key.strip().lower())

    def put(self, natural_key: str, report: HealthReport) -> HealthReport:
        with self._lock():
            records = self._read_unlocked()
            records[natural_key.strip().lower()] = report
            self._write_unlocked(records)
        return report


def connection_state_from_findings(
    findings: Tuple[HealthFinding, ...],
) -> FleetNodeConnectionState:
    """Deterministically derive a connection state from health findings.

    Ordered most-severe-first: a dead/expired node is DISCONNECTED even if
    it also happens to be stale; any other finding is DEGRADED; no findings
    is CONNECTED.
    """
    if HealthFinding.EXPIRED in findings or HealthFinding.NOT_ALIVE in findings:
        return FleetNodeConnectionState.DISCONNECTED
    if HealthFinding.STALE in findings:
        return FleetNodeConnectionState.STALE
    if findings:
        return FleetNodeConnectionState.DEGRADED
    return FleetNodeConnectionState.CONNECTED


class HeartbeatService:
    """Governed, fail-closed heartbeat ingestion wired to a fleet registry."""

    def __init__(
        self,
        registry: FleetNodeRegistry,
        health_store: Optional[HealthReportStore] = None,
        *,
        max_report_age_seconds: int = DEFAULT_MAX_REPORT_AGE_SECONDS,
        report_expiry_seconds: int = DEFAULT_HEARTBEAT_EXPIRY_SECONDS,
    ) -> None:
        self._registry = registry
        self._health_store = health_store or HealthReportStore()
        self._max_report_age_seconds = max_report_age_seconds
        self._report_expiry_seconds = report_expiry_seconds

    def ingest(self, submission: HeartbeatSubmission, *, now: int) -> HeartbeatResult:
        natural_key = submission._normalize_key()

        def _result(
            outcome: HeartbeatOutcome,
            *,
            rejection_code: Optional[HeartbeatRejectionCode] = None,
            connection_state: Optional[FleetNodeConnectionState] = None,
            previous_connection_state: Optional[FleetNodeConnectionState] = None,
            findings: Tuple[HealthFinding, ...] = (),
            health_report_id: Optional[str] = None,
        ) -> HeartbeatResult:
            return HeartbeatResult(
                outcome=outcome,
                rejection_code=rejection_code,
                natural_key=natural_key,
                connection_state=connection_state,
                previous_connection_state=previous_connection_state,
                findings=findings,
                health_report_id=health_report_id,
                decided_at=now,
            )

        record = self._registry.get(natural_key)
        if record is None:
            return _result(
                HeartbeatOutcome.REJECTED,
                rejection_code=HeartbeatRejectionCode.UNKNOWN_NODE,
            )
        if record.revoked:
            return _result(
                HeartbeatOutcome.REJECTED,
                rejection_code=HeartbeatRejectionCode.NODE_REVOKED,
                connection_state=FleetNodeConnectionState.REVOKED,
                previous_connection_state=record.connection_state,
            )
        if submission.submitted_at > now:
            return _result(
                HeartbeatOutcome.REJECTED,
                rejection_code=HeartbeatRejectionCode.SUBMITTED_IN_FUTURE,
                previous_connection_state=record.connection_state,
            )

        payload = {
            "identity_id": record.identity_id,
            "natural_key": natural_key,
            "observed_at": submission.submitted_at,
        }
        report = HealthReport(
            report_id=f"health_{_digest(payload)[:24]}",
            subject_identity_id=record.identity_id,
            observed_at=submission.submitted_at,
            expires_at=submission.submitted_at + self._report_expiry_seconds,
            liveness=submission.liveness,
            readiness=submission.readiness,
            dependency_health=submission.dependency_health,
            degradation=submission.degradation,
            quarantine=submission.quarantine,
            checks=submission.checks,
            correlation_id=submission.correlation_id,
        )
        findings = evaluate_health(
            report, now=now, max_age_seconds=self._max_report_age_seconds
        )
        connection_state = connection_state_from_findings(findings)

        self._health_store.put(natural_key, report)
        previous_state = record.connection_state
        updated_record = record.model_copy(
            update={
                "connection_state": connection_state,
                "last_seen_at": submission.submitted_at,
                "last_health_report_id": report.report_id,
                "model_inventory": (
                    submission.reported_model_inventory
                    if submission.reported_model_inventory
                    else record.model_inventory
                ),
                "updated_at": now,
            }
        )
        self._registry._store.put(updated_record)  # noqa: SLF001 - same-package composition

        return _result(
            HeartbeatOutcome.ACCEPTED,
            connection_state=connection_state,
            previous_connection_state=previous_state,
            findings=findings,
            health_report_id=report.report_id,
        )

    def latest_health(self, natural_key: str) -> Optional[HealthReport]:
        return self._health_store.get(natural_key.strip().lower())

    def current_connection_state(
        self, natural_key: str, *, now: int
    ) -> FleetNodeConnectionState:
        """Always re-derived from the stored report against ``now``.

        Never reads ``FleetNodeRecord.connection_state`` directly — that
        field only reflects the state as of the last ingested heartbeat, and
        a node that has simply stopped heartbeating must still be
        recognized as no-longer-healthy the moment enough time has passed,
        without needing another event to push it there.
        """
        natural_key = natural_key.strip().lower()
        record = self._registry.get(natural_key)
        if record is None:
            return FleetNodeConnectionState.UNKNOWN
        if record.revoked:
            return FleetNodeConnectionState.REVOKED
        report = self._health_store.get(natural_key)
        if report is None:
            return FleetNodeConnectionState.UNKNOWN
        findings = evaluate_health(report, now=now, max_age_seconds=self._max_report_age_seconds)
        return connection_state_from_findings(findings)

    def is_usable_for_dispatch(self, natural_key: str, *, now: int) -> bool:
        """Fail-closed dispatch-eligibility gate.

        True only for a registered, non-revoked node whose most recent
        heartbeat is fresh and fully healthy right now. Anything else
        (unregistered, revoked, never heartbeated, stale, degraded,
        disconnected) is not usable, never merely "probably fine".
        """
        natural_key = natural_key.strip().lower()
        if not self._registry.is_admissible_node(natural_key):
            return False
        return (
            self.current_connection_state(natural_key, now=now)
            == FleetNodeConnectionState.CONNECTED
        )
