"""Qualification framework (spec sections 4, 5): orchestrates provider
lifecycle transitions from canary results, and keeps "qualified" and
"authorized to route real traffic" as two distinct, separately-recorded
steps.

Passing canaries only ever gets a provider to :attr:`LifecycleState.QUALIFIED`.
Reaching :attr:`LifecycleState.APPROVED` — the only state
:mod:`runway.scoring` will route non-synthetic traffic to — requires a
separate, explicit :meth:`QualificationService.authorize` call. Nothing in
this module calls that automatically.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from runway import storage
from runway.canary import CanaryOutcome, ModelClient, run_canary_suite
from runway.flags import RunwayFeatureFlags
from runway.lifecycle import InvalidLifecycleTransition, LifecycleState, QuarantineReason, transition
from runway.registry import CanaryStatus, ModelRecord, Registry

_SCHEMA_VERSION = 1
_COMPONENT = "qualification_history"

_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS canary_history ("
    " canary_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " model_key TEXT NOT NULL,"
    " suite_version INTEGER NOT NULL,"
    " executed_at INTEGER NOT NULL,"
    " pass_rate INTEGER NOT NULL,"
    " status TEXT NOT NULL,"
    " failure_reason TEXT,"
    " suspected_identity_mismatch INTEGER NOT NULL"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_canary_history_model ON canary_history(model_key)",
    "CREATE TABLE IF NOT EXISTS provider_lifecycle_history ("
    " transition_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " provider_id TEXT NOT NULL,"
    " from_state TEXT NOT NULL,"
    " to_state TEXT NOT NULL,"
    " reason TEXT NOT NULL,"
    " timestamp INTEGER NOT NULL"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_provider_lifecycle_history_provider ON provider_lifecycle_history(provider_id)",
)

#: Mechanical, authorization-free advancement toward SYNTHETIC_TESTING. Every
#: step here is itself a legal transition per runway.lifecycle — this table
#: just sequences them so callers don't hand-walk the graph.
_ADVANCE_TO_TESTING: dict[LifecycleState, Tuple[LifecycleState, ...]] = {
    LifecycleState.DISCOVERED: (LifecycleState.PENDING_QUALIFICATION, LifecycleState.SYNTHETIC_TESTING),
    LifecycleState.PENDING_QUALIFICATION: (LifecycleState.SYNTHETIC_TESTING,),
    LifecycleState.QUARANTINED: (LifecycleState.SYNTHETIC_TESTING,),
    LifecycleState.QUALIFIED: (LifecycleState.SYNTHETIC_TESTING,),
    LifecycleState.SYNTHETIC_TESTING: (),
}


class QualificationError(Exception):
    pass


class QualificationHistoryStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        storage.ensure_schema(conn, component=_COMPONENT, version=_SCHEMA_VERSION, statements=_SCHEMA_STATEMENTS)

    @classmethod
    def open(cls, *, path: Optional[Path] = None) -> "QualificationHistoryStore":
        return cls(storage.connect(path))

    def record_canary(self, outcome: CanaryOutcome) -> None:
        self._conn.execute(
            "INSERT INTO canary_history "
            "(model_key, suite_version, executed_at, pass_rate, status, failure_reason, suspected_identity_mismatch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                outcome.model_key, outcome.suite_version, outcome.executed_at, outcome.pass_rate,
                outcome.status.value, outcome.failure_reason, int(outcome.suspected_identity_mismatch),
            ),
        )
        self._conn.commit()

    def record_lifecycle_transition(
        self, *, provider_id: str, from_state: LifecycleState, to_state: LifecycleState, reason: str, timestamp: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO provider_lifecycle_history (provider_id, from_state, to_state, reason, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider_id, from_state.value, to_state.value, reason, timestamp),
        )
        self._conn.commit()

    def lifecycle_history(self, provider_id: str) -> Tuple[dict, ...]:
        rows = self._conn.execute(
            "SELECT from_state, to_state, reason, timestamp FROM provider_lifecycle_history "
            "WHERE provider_id = ? ORDER BY transition_id ASC",
            (provider_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)


class QualificationService:
    def __init__(self, history: QualificationHistoryStore, *, flags: RunwayFeatureFlags) -> None:
        self._history = history
        self._flags = flags

    def _transition_provider(
        self, registry: Registry, provider_id: str, target: LifecycleState, *, reason: str, now: int
    ) -> Registry:
        provider = registry.provider(provider_id)
        try:
            new_state = transition(provider.lifecycle_state, target)
        except InvalidLifecycleTransition:
            raise
        self._history.record_lifecycle_transition(
            provider_id=provider_id, from_state=provider.lifecycle_state, to_state=new_state,
            reason=reason, timestamp=now,
        )
        return registry.with_provider(provider.model_copy(update={"lifecycle_state": new_state}))

    def run_qualification(
        self,
        registry: Registry,
        model: ModelRecord,
        client: ModelClient,
        *,
        now: int,
        expected_identity_fingerprint: Optional[str] = None,
    ) -> Tuple[Registry, CanaryOutcome]:
        if not self._flags.synthetic_qualification_enabled:
            raise QualificationError("synthetic_qualification_enabled flag is off")

        provider = registry.provider(model.provider_id)
        outcome = run_canary_suite(
            client, model_key=model.model_key, expected=model.capability_profile,
            expected_identity_fingerprint=expected_identity_fingerprint, now=now,
        )
        self._history.record_canary(outcome)

        registry = registry.with_model(model.model_copy(update={
            "last_canary_at": now,
            "canary_status": outcome.status,
            "canary_suite_version": outcome.suite_version,
            "canary_pass_rate": outcome.pass_rate,
            "canary_failure_reason": outcome.failure_reason,
            "suspected_identity_mismatch": outcome.suspected_identity_mismatch,
        }))

        if provider.lifecycle_state in (LifecycleState.APPROVED, LifecycleState.DEGRADED):
            if outcome.status != CanaryStatus.PASSED:
                reason = (
                    QuarantineReason.IDENTITY_MISMATCH if outcome.suspected_identity_mismatch
                    else QuarantineReason.CANARY_REGRESSION
                )
                registry = self._transition_provider(
                    registry, provider.provider_id, LifecycleState.QUARANTINED, reason=reason.value, now=now
                )
            return registry, outcome

        for step in _ADVANCE_TO_TESTING.get(provider.lifecycle_state, ()):
            registry = self._transition_provider(
                registry, provider.provider_id, step, reason="qualification_in_progress", now=now
            )

        if outcome.status == CanaryStatus.PASSED:
            registry = self._transition_provider(
                registry, provider.provider_id, LifecycleState.QUALIFIED, reason="canary_passed", now=now
            )
        return registry, outcome

    def authorize(self, registry: Registry, provider_id: str, *, now: int, authorized_by: str) -> Registry:
        """The one and only path to APPROVED. Passing qualification never
        implies authorization by itself (spec section 4 & 5) — this is a
        separate, explicit, auditable action.
        """
        provider = registry.provider(provider_id)
        if provider.lifecycle_state != LifecycleState.QUALIFIED:
            raise QualificationError(
                f"provider {provider_id!r} is {provider.lifecycle_state.value!r}, "
                "must be qualified before it can be authorized"
            )
        if not authorized_by.strip():
            raise QualificationError("authorize() requires a non-empty authorized_by identity")
        return self._transition_provider(
            registry, provider_id, LifecycleState.APPROVED,
            reason=f"authorized_by:{authorized_by.strip()}", now=now,
        )
