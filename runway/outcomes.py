"""Historical route performance (spec section 13).

Recorded per ``task_type x model_key`` — and since :class:`~runway.registry.ModelRecord.model_key`
already encodes the provider (Principle B), this is exactly the
"task class x model x provider" granularity the spec asks for, with no
extra join key needed.

Success here means *verified* success (spec Principle G — "a route is not
successful merely because it returned HTTP 200"): callers record
``verifier_passed`` explicitly rather than this store inferring success from
the absence of an error.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from runway import storage

_SCHEMA_VERSION = 1
_COMPONENT = "task_outcomes"

_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS task_outcomes ("
    " outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " timestamp INTEGER NOT NULL,"
    " task_type TEXT NOT NULL,"
    " model_key TEXT NOT NULL,"
    " provider_id TEXT NOT NULL,"
    " input_tokens INTEGER NOT NULL,"
    " output_tokens INTEGER NOT NULL,"
    " cached_input_tokens INTEGER NOT NULL,"
    " latency_ms INTEGER NOT NULL,"
    " estimated_cost_micros INTEGER NOT NULL,"
    " actual_cost_micros INTEGER,"
    " verifier_passed INTEGER NOT NULL,"
    " network_failure INTEGER NOT NULL,"
    " provider_error INTEGER NOT NULL,"
    " validation_failure INTEGER NOT NULL,"
    " retried INTEGER NOT NULL,"
    " escalated_to TEXT"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_task_outcomes_group ON task_outcomes(task_type, model_key)",
)


class TaskOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_type: str
    model_key: str
    provider_id: str
    timestamp: int = Field(..., ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    estimated_cost_micros: int = Field(default=0, ge=0)
    actual_cost_micros: Optional[int] = Field(default=None, ge=0)
    verifier_passed: bool
    network_failure: bool = False
    provider_error: bool = False
    validation_failure: bool = False
    retried: bool = False
    escalated_to: Optional[str] = None


class PerformanceAggregate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_type: str
    model_key: str
    attempts: int
    successes: int
    retries: int
    escalations: int
    avg_cost_micros: int
    cost_per_success_micros: Optional[int]
    p50_latency_ms: int
    p95_latency_ms: int

    @property
    def success_rate(self) -> float:
        return 0.0 if self.attempts == 0 else self.successes / self.attempts

    @property
    def retry_rate(self) -> float:
        return 0.0 if self.attempts == 0 else self.retries / self.attempts

    @property
    def escalation_rate(self) -> float:
        return 0.0 if self.attempts == 0 else self.escalations / self.attempts


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


class HistoricalPerformanceStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        storage.ensure_schema(conn, component=_COMPONENT, version=_SCHEMA_VERSION, statements=_SCHEMA_STATEMENTS)

    @classmethod
    def open(cls, *, path: Optional[Path] = None) -> "HistoricalPerformanceStore":
        return cls(storage.connect(path))

    def record(self, outcome: TaskOutcome) -> None:
        self._conn.execute(
            "INSERT INTO task_outcomes ("
            " timestamp, task_type, model_key, provider_id, input_tokens, output_tokens,"
            " cached_input_tokens, latency_ms, estimated_cost_micros, actual_cost_micros,"
            " verifier_passed, network_failure, provider_error, validation_failure, retried, escalated_to"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                outcome.timestamp, outcome.task_type, outcome.model_key, outcome.provider_id,
                outcome.input_tokens, outcome.output_tokens, outcome.cached_input_tokens,
                outcome.latency_ms, outcome.estimated_cost_micros, outcome.actual_cost_micros,
                int(outcome.verifier_passed), int(outcome.network_failure), int(outcome.provider_error),
                int(outcome.validation_failure), int(outcome.retried), outcome.escalated_to,
            ),
        )
        self._conn.commit()

    def aggregate(self, *, task_type: str, model_key: str) -> PerformanceAggregate:
        rows = self._conn.execute(
            "SELECT latency_ms, estimated_cost_micros, actual_cost_micros, verifier_passed, retried, escalated_to "
            "FROM task_outcomes WHERE task_type = ? AND model_key = ?",
            (task_type, model_key),
        ).fetchall()

        attempts = len(rows)
        successes = sum(1 for row in rows if row["verifier_passed"])
        retries = sum(1 for row in rows if row["retried"])
        escalations = sum(1 for row in rows if row["escalated_to"])
        costs = [row["actual_cost_micros"] if row["actual_cost_micros"] is not None else row["estimated_cost_micros"]
                 for row in rows]
        latencies = sorted(row["latency_ms"] for row in rows)
        success_costs = [
            (row["actual_cost_micros"] if row["actual_cost_micros"] is not None else row["estimated_cost_micros"])
            for row in rows if row["verifier_passed"]
        ]

        return PerformanceAggregate(
            task_type=task_type, model_key=model_key, attempts=attempts, successes=successes,
            retries=retries, escalations=escalations,
            avg_cost_micros=(sum(costs) // attempts) if attempts else 0,
            cost_per_success_micros=(sum(success_costs) // successes) if successes else None,
            p50_latency_ms=_percentile(latencies, 0.50),
            p95_latency_ms=_percentile(latencies, 0.95),
        )

    def success_probability(
        self, *, task_type: str, model_key: str, prior: float, prior_weight: int = 5
    ) -> float:
        """Bayesian-shrunk success probability: with few or no historical
        attempts, trust the (quality-derived) prior; as attempts accumulate,
        trust the observed rate. This is what lets Runway skip a cheap model
        with a proven poor track record on a task type (Principle F) without
        a hardcoded cheap-first ladder.
        """
        aggregate = self.aggregate(task_type=task_type, model_key=model_key)
        numerator = aggregate.successes + prior * prior_weight
        denominator = aggregate.attempts + prior_weight
        return numerator / denominator
