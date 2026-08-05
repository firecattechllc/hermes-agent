"""Whole-system knowledge graph → Mission Control visibility bridge.

Fleet Unification Stage 2A / Stage 9. ``hermes_cli.knowledge`` (Step 33, the
whole-system knowledge graph) was previously completely isolated from
``hermes_cli.mission_control`` — no file in the ``knowledge`` package
imported or referenced Mission Control. The event types this bridge uses,
``knowledge_snapshot_recorded`` and ``knowledge_drift_recorded``, were
already reserved in ``hermes_cli.mission_control.models``'s closed
``_TELEMETRY_EVENT_TYPES`` set (added alongside the rest of Step 33) but were
never actually published by anything — this module is what finally wires
them up, following the exact ``*VisibilityAdapter`` /
``*VisibilityService`` pattern used by every other governed subsystem (see
``hermes_cli.agent_roles.model_routing_visibility`` for the canonical
example).

This module is read-only with respect to the knowledge graph: it never
mutates ``KnowledgeGraphStore`` state, it only projects already-collected
entities/relationships/changes into Mission Control telemetry events.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional, Sequence

from hermes_cli.knowledge.models import (
    GraphChange,
    KnowledgeEntity,
    KnowledgeRelationship,
)
from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

KNOWLEDGE_SNAPSHOT_EVENT = "knowledge_snapshot_recorded"
KNOWLEDGE_DRIFT_EVENT = "knowledge_drift_recorded"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class KnowledgeVisibilityAdapter:
    """Builds Mission Control telemetry events from knowledge graph records."""

    def to_snapshot_event(
        self,
        *,
        project_id: str,
        node_id: str,
        entities: Sequence[KnowledgeEntity],
        relationships: Sequence[KnowledgeRelationship],
        timestamp: int,
        correlation_id: Optional[str] = None,
    ) -> mission_models.TelemetryEvent:
        entity_ids = tuple(sorted(entity.entity_id for entity in entities))
        relationship_ids = tuple(sorted(rel.relationship_id for rel in relationships))
        key = _digest({
            "node_id": node_id,
            "entities": entity_ids,
            "relationships": relationship_ids,
        })
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_knowledge_{key[:24]}",
            event_type=KNOWLEDGE_SNAPSHOT_EVENT,
            project_id=project_id,
            agent_id=node_id,
            timestamp=timestamp,
            severity="info",
            correlation_id=correlation_id,
            payload={
                "source": "knowledge_graph",
                "node_id": node_id,
                "entity_count": len(entities),
                "relationship_count": len(relationships),
                "entity_ids": list(entity_ids),
                "relationship_ids": list(relationship_ids),
                "source_idempotency_key": f"knowledge_snapshot:{key}",
            },
        )

    def to_drift_event(
        self,
        *,
        project_id: str,
        node_id: str,
        change: GraphChange,
        correlation_id: Optional[str] = None,
    ) -> mission_models.TelemetryEvent:
        severity = (
            "warning" if change.severity.value in {"high", "critical"} else "info"
        )
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_knowledge_change_{change.change_id}",
            event_type=KNOWLEDGE_DRIFT_EVENT,
            project_id=project_id,
            agent_id=node_id,
            timestamp=int(change.detected_at.timestamp()),
            severity=severity,
            correlation_id=correlation_id,
            payload={
                "source": "knowledge_graph",
                "node_id": node_id,
                "change": change.model_dump(mode="json"),
                "source_idempotency_key": f"knowledge_drift:{change.change_id}",
            },
        )


class KnowledgeVisibilityService:
    """Publishes knowledge graph snapshots and drift into Mission Control."""

    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = KnowledgeVisibilityAdapter()

    def publish_snapshot(
        self,
        *,
        project_id: str,
        node_id: str,
        entities: Sequence[KnowledgeEntity],
        relationships: Sequence[KnowledgeRelationship],
        timestamp: int,
        correlation_id: Optional[str] = None,
    ) -> mission_models.TelemetryEvent:
        event = self._adapter.to_snapshot_event(
            project_id=project_id,
            node_id=node_id,
            entities=entities,
            relationships=relationships,
            timestamp=timestamp,
            correlation_id=correlation_id,
        )
        return self._mission_control.append_event_once(event) or event

    def publish_drift(
        self,
        *,
        project_id: str,
        node_id: str,
        changes: Iterable[GraphChange],
        correlation_id: Optional[str] = None,
    ) -> list[mission_models.TelemetryEvent]:
        published = []
        for change in changes:
            event = self._adapter.to_drift_event(
                project_id=project_id,
                node_id=node_id,
                change=change,
                correlation_id=correlation_id,
            )
            published.append(self._mission_control.append_event_once(event) or event)
        return published
