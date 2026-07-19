"""Mission Control telemetry to Structured Engineering Memory adapter.

Telemetry is intentionally filtered. Routine operational events do not become
long-term memory. Only events that contain durable lessons, failures, risks,
decisions, evidence conclusions, or promotion outcomes are eligible.

Every imported event creates a candidate memory only.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Iterable, Optional, Sequence

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.service import EngineeringMemoryService

logger = logging.getLogger(
    "hermes.engineering_memory.adapters.mission_control"
)


_ELIGIBLE_EVENT_TYPES = frozenset(
    {
        "launch_failed",
        "agent_failed",
        "task_failed",
        "evidence_recorded",
        "evidence_updated",
        "approval_resolved",
        "promotion_approved",
        "promotion_rejected",
        "promotion_completed",
        "policy_violation",
        "security_finding",
        "lesson_captured",
        "decision_recorded",
        "risk_identified",
        "incident_recorded",
    }
)


def _required_attr(obj: Any, attr: str, label: str) -> Any:
    value = getattr(obj, attr, None)
    if value in (None, ""):
        raise ValueError(f"{label} missing required {attr}")
    return value


def _value_text(value: Any) -> str:
    if value is None:
        return ""

    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)

    return str(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return [value] if value else []

    if isinstance(value, Sequence):
        return [
            str(item)
            for item in value
            if item not in (None, "")
        ]

    return [str(value)]


def _stable_memory_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(
        str(part)
        for part in parts
        if part not in (None, "")
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _resolve_enum_value(
    enum_class: Any,
    preferred_values: Iterable[str],
) -> Any:
    for value in preferred_values:
        try:
            return enum_class(value)
        except ValueError:
            continue

    members = list(enum_class)
    if not members:
        raise ValueError(f"{enum_class.__name__} defines no values")

    return members[0]


def _mission_control_source_type() -> m.MemorySourceType:
    return _resolve_enum_value(
        m.MemorySourceType,
        (
            "mission_control",
            "telemetry",
            "system",
            "human",
        ),
    )


def _memory_type_for_event(event_type: str) -> m.MemoryType:
    if event_type in {
        "launch_failed",
        "agent_failed",
        "task_failed",
        "incident_recorded",
    }:
        preferred = (
            "failure_pattern",
            "known_risk",
            "implementation_lesson",
        )
    elif event_type in {
        "policy_violation",
        "security_finding",
        "risk_identified",
    }:
        preferred = (
            "known_risk",
            "failure_pattern",
            "implementation_lesson",
        )
    elif event_type in {
        "approval_resolved",
        "promotion_approved",
        "promotion_rejected",
        "promotion_completed",
        "decision_recorded",
    }:
        preferred = (
            "architecture_decision",
            "implementation_lesson",
        )
    else:
        preferred = (
            "implementation_lesson",
        )

    return _resolve_enum_value(m.MemoryType, preferred)


def _first_payload_text(
    payload: Dict[str, Any],
    *keys: str,
) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


class MissionControlMemoryAdapter:
    """Translate selected telemetry events into candidate memories."""

    @property
    def eligible_event_types(self) -> frozenset[str]:
        return _ELIGIBLE_EVENT_TYPES

    def is_eligible(self, event: Any) -> bool:
        event_type = _value_text(
            getattr(event, "event_type", "")
        )
        payload = getattr(event, "payload", None)

        if not isinstance(payload, dict):
            return False

        if event_type in _ELIGIBLE_EVENT_TYPES:
            return True

        return bool(payload.get("memory_candidate"))

    def ingest_event(
        self,
        service: EngineeringMemoryService,
        event: Any,
        *,
        actor: str = "mission_control",
    ) -> Optional[m.MemoryRecord]:
        if not self.is_eligible(event):
            return None

        event_id = str(
            _required_attr(event, "event_id", "telemetry event")
        )
        project_id = str(
            _required_attr(event, "project_id", "telemetry event")
        )
        event_type = _value_text(
            _required_attr(event, "event_type", "telemetry event")
        )

        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            raise ValueError(
                f"telemetry event {event_id} payload must be an object"
            )

        title = _first_payload_text(
            payload,
            "memory_title",
            "title",
            "summary",
            "failure_reason",
            "reason",
            "message",
        )
        if title is None:
            title = event_type.replace("_", " ").title()

        summary = _first_payload_text(
            payload,
            "memory_summary",
            "lesson",
            "decision",
            "finding",
            "failure_reason",
            "reason",
            "summary",
            "message",
        )
        if summary is None:
            summary = (
                f"Mission Control recorded {event_type} "
                f"in project {project_id}."
            )

        evidence_refs = _string_list(
            payload.get("evidence_refs")
        )

        source_path = payload.get("source_path")
        if source_path not in (None, ""):
            evidence_refs.append(str(source_path))

        evidence_id = payload.get("evidence_id")
        if evidence_id not in (None, ""):
            evidence_refs.append(
                f"mission_control_evidence:{evidence_id}"
            )

        timestamp = getattr(event, "timestamp", None) or m._utc_now()

        provenance = m.MemoryProvenance(
            source_type=_mission_control_source_type(),
            source_ids=(f"telemetry_event:{event_id}",),
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
            captured_at=int(timestamp),
            captured_by=actor,
        )

        launch_id = getattr(event, "launch_id", None)
        task_id = getattr(event, "task_id", None)
        backlog_id = getattr(event, "backlog_id", None)
        agent_id = getattr(event, "agent_id", None)
        correlation_id = getattr(event, "correlation_id", None)
        causation_id = getattr(event, "causation_id", None)

        structured_payload: Dict[str, Any] = {
            "source_domain": "mission_control",
            "source_event_id": event_id,
            "source_event_type": event_type,
            "severity": _value_text(
                getattr(event, "severity", "")
            ),
            "launch_id": launch_id,
            "task_id": task_id,
            "backlog_id": backlog_id,
            "agent_id": agent_id,
            "telemetry_payload": payload,
        }

        tags = [
            "mission-control",
            event_type,
        ]
        tags.extend(_string_list(payload.get("tags")))

        source_key = ":".join(
            [
                "mission_control_memory",
                project_id,
                event_id,
            ]
        )

        memory = service.create_candidate(
            project_id,
            _memory_type_for_event(event_type),
            title,
            summary,
            provenance=provenance,
            body=_first_payload_text(
                payload,
                "memory_body",
                "details",
                "description",
            ),
            structured_payload=structured_payload,
            confidence=payload.get("memory_confidence"),
            tags=tags,
            created_by=actor,
            actor=actor,
            memory_id=_stable_memory_id(
                "mem",
                "mission_control",
                project_id,
                event_id,
            ),
            source_idempotency_key=source_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        if memory.status != m.MemoryStatus.CANDIDATE:
            raise ValueError(
                "Mission Control adapter may only produce candidate memories"
            )

        logger.info(
            "ingested telemetry event %s as candidate memory %s "
            "for project %s",
            event_id,
            memory.memory_id,
            project_id,
        )
        return memory

    def ingest_events(
        self,
        service: EngineeringMemoryService,
        events: Iterable[Any],
        *,
        actor: str = "mission_control",
    ) -> list[m.MemoryRecord]:
        memories: list[m.MemoryRecord] = []

        for event in events:
            memory = self.ingest_event(
                service,
                event,
                actor=actor,
            )
            if memory is not None:
                memories.append(memory)

        return memories
