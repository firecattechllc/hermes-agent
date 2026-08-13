"""Structured observability events (spec section 22).

Self-contained: this is shaped like ``hermes_cli.mission_control``'s
``TelemetryEvent`` (append-only, immutable, explicit event types) but does
not import or write to Mission Control, whose ``event_type`` vocabulary is a
closed enum in a shared file this phase deliberately leaves untouched (see
docs). A later integration phase can register these event types there and
swap the sink; nothing in Runway's callers would need to change.

Every event is validated to contain no prompt bodies, no source code, and
nothing secret-shaped before it is ever written.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hermes_constants import get_hermes_home
from runway.identifiers import contains_sensitive_marker


class RunwayEventType(str, Enum):
    CANDIDATE_DISCOVERED = "candidate_discovered"
    QUALIFICATION_STARTED = "qualification_started"
    QUALIFICATION_PASSED = "qualification_passed"
    QUALIFICATION_FAILED = "qualification_failed"
    PROVIDER_QUARANTINED = "provider_quarantined"
    PROVIDER_RESTORED = "provider_restored"
    ROUTE_CANDIDATES_EVALUATED = "route_candidates_evaluated"
    ROUTE_SELECTED = "route_selected"
    ROUTE_REJECTED_BY_POLICY = "route_rejected_by_policy"
    BUDGET_REJECTED = "budget_rejected"
    TASK_EXECUTED = "task_executed"
    VERIFIER_PASSED = "verifier_passed"
    VERIFIER_FAILED = "verifier_failed"
    RETRY = "retry"
    ESCALATION = "escalation"
    ENDPOINT_FAILOVER = "endpoint_failover"
    CANARY_REGRESSION = "canary_regression"


_FORBIDDEN_PAYLOAD_KEYS = frozenset({"prompt", "messages", "source_code", "api_key", "credential"})


class RunwayEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: RunwayEventType
    timestamp: int = Field(..., ge=0)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sanitized(self) -> "RunwayEvent":
        for key in self.payload:
            if key.lower() in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"runway telemetry payload must not contain {key!r}")
        encoded = json.dumps(self.payload, sort_keys=True, default=str)
        if contains_sensitive_marker(encoded):
            raise ValueError("runway telemetry payload contains sensitive content")
        return self


class RunwayTelemetryLog:
    """Append-only JSONL journal, one line per event. Deliberately dumb and
    local — no external sink in this phase (spec section 13: "keep telemetry
    local").
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (get_hermes_home() / "runway" / "telemetry.jsonl")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: RunwayEvent) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def read_all(self) -> tuple[RunwayEvent, ...]:
        if not self._path.exists():
            return ()
        events = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(RunwayEvent.model_validate_json(line))
        return tuple(events)
