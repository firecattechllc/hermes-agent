"""Context engine integration adapter — translates context engine domain
models into Mission Control TelemetryEvent records.

This adapter translates :mod:`hermes_cli.context_engine.models` entities into
:mod:`hermes_cli.mission_control.models.TelemetryEvent` instances, enabling
the mission control store to ingest context engine state without coupling the
two packages.

Mission Control does NOT import from context_engine at the module level. This
adapter takes concrete instances (duck-typing compatible with the expected
attributes) so that a forward reference or delayed import path works without
creating a circular import at the package level.

Usage:
    adapter = ContextAdapter()
    events = adapter.translate_launch_to_events(launch, project=project)
    service.ingest_context_launch(launch, project=project, records=records)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_cli.mission_control import models as m

logger = logging.getLogger("hermes.mission_control.adapters.context")


def _required_attr(obj: Any, attr: str, label: str) -> Any:
    value = getattr(obj, attr, None)
    if value in (None, ""):
        raise ValueError(f"{label} missing required {attr}")
    return value


def _idem_key(*parts: Any) -> str:
    return ":".join(str(part) for part in parts if part not in (None, ""))


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


# ── Context Adapter ─────────────────────────────────────────────────────────

class ContextAdapter:
    """Translates context engine domain objects into TelemetryEvent instances.

    This adapter defines import-time-free translation functions. Concrete
    instances (already constructed by context_engine) are passed in; this
    adapter accesses attributes via standard dot access (duck-typing).

    No context_engine imports exist at module level. If you need to construct
    context_engine models inside this adapter, import them inside the method
    body.
    """

    def translate_launch_to_events(
        self,
        launch: Any,
        project: Optional[Any] = None,
        actor: str = "context_engine",
    ) -> List[m.TelemetryEvent]:
        """Translate a context engine LaunchContext into a list of TelemetryEvents.

        Produces:
        - One ``context_launch_imported`` event per launch_id
        - One ``agent_started`` event per selected_agent
        - One ``backlog_item_created`` event if backlog_id is set
        - One ``evidence_requested`` event per evidence_ref
        - One ``promotion_requested`` event if promotion_state is set
        - One ``context_ingested`` summary event

        Args:
            launch: A LaunchContext-like object (duck-typed).
            project: Optional Project-like object for registration events.
            actor: Actor label for provenance.

        Returns:
            List of TelemetryEvent instances. These will be idempotency-checked
            by the service layer before persistence.
        """
        project_id = _required_attr(launch, "project_id", "launch")
        launch_id = _required_attr(launch, "launch_id", "launch")
        now = m._utc_now()
        launch_updated_at = getattr(launch, "updated_at", None)
        launch_status = _value_text(getattr(launch, "status", ""))
        launch_stage = _value_text(getattr(launch, "stage", ""))

        events: List[m.TelemetryEvent] = []
        base_correlation_id = m.new_telemetry_event_id()
        sequence = 0

        # ── Project registration event ─────────────────────────────────────
        if project is not None:
            sequence += 1
            project_id_val = getattr(project, "project_id", project_id)
            display_name = getattr(project, "display_name", project_id_val)
            events.append(m.TelemetryEvent(
                event_id=m.new_telemetry_event_id(),
                event_type="context_ingested",
                project_id=project_id_val,
                launch_id=launch_id,
                severity="info",
                correlation_id=base_correlation_id,
                causation_id=None,
                payload={
                    "source": "context_engine",
                    "source_event_type": "project_registered",
                    "display_name": display_name,
                    "repository_identity": getattr(project, "repository_identity", None),
                    "agent": actor,
                    "source_idempotency_key": _idem_key(
                        "context_project", project_id_val, launch_id
                    ),
                },
            ))

        # ── Launch import event ────────────────────────────────────────────
        sequence += 1
        launch_import_event = m.TelemetryEvent(
            event_id=m.new_telemetry_event_id(),
            event_type="context_launch_imported",
            project_id=project_id,
            launch_id=launch_id,
            task_id=getattr(launch, "task_id", None),
            backlog_id=getattr(launch, "backlog_id", None),
            severity="info",
            correlation_id=base_correlation_id,
            causation_id=None,
            payload={
                "source": "context_engine",
                "source_event_type": "launch_started",
                "launch_id": launch_id,
                "stage": launch_stage,
                "status": launch_status,
                "failure_reason": getattr(launch, "failure_reason", None),
                "agent": actor,
                "source_idempotency_key": _idem_key(
                    "context_launch", project_id, launch_id,
                    launch_status, launch_stage, launch_updated_at,
                ),
            },
        )
        events.append(launch_import_event)

        # ── Agent start events ────────────────────────────────────────────
        selected_agents = getattr(launch, "selected_agents", [])
        for agent_slug in selected_agents:
            sequence += 1
            agent_id = "agnt_" + agent_slug[:12] if agent_slug else "agnt_unknown"
            events.append(m.TelemetryEvent(
                event_id=m.new_telemetry_event_id(),
                event_type="agent_started",
                project_id=project_id,
                launch_id=launch_id,
                task_id=getattr(launch, "task_id", None),
                backlog_id=getattr(launch, "backlog_id", None),
                agent_id=agent_id,
                severity="info",
                correlation_id=base_correlation_id,
                causation_id=launch_import_event.event_id,
                payload={
                    "agent_slug": agent_slug,
                    "source": "context_engine",
                    "agent": actor,
                    "source_idempotency_key": _idem_key(
                        "context_launch_agent", project_id, launch_id, agent_slug
                    ),
                },
            ))

        # ── Backlog item event ──────────────────────────────────────────────
        backlog_id = getattr(launch, "backlog_id", None)
        if backlog_id:
            sequence += 1
            events.append(m.TelemetryEvent(
                event_id=m.new_telemetry_event_id(),
                event_type="backlog_item_created",
                project_id=project_id,
                launch_id=launch_id,
                task_id=getattr(launch, "task_id", None),
                backlog_id=backlog_id,
                severity="info",
                correlation_id=base_correlation_id,
                causation_id=launch_import_event.event_id,
                payload={
                    "source": "context_engine",
                    "title": f"Launch {launch_id} task",
                    "agent": actor,
                    "source_idempotency_key": _idem_key(
                        "context_launch_backlog", project_id, launch_id, backlog_id
                    ),
                },
            ))

        # ── Evidence events ────────────────────────────────────────────────
        evidence_refs = getattr(launch, "evidence_refs", [])
        for ref in evidence_refs:
            sequence += 1
            evidence_id = m.new_evidence_id()
            events.append(m.TelemetryEvent(
                event_id=m.new_telemetry_event_id(),
                event_type="evidence_requested",
                project_id=project_id,
                launch_id=launch_id,
                task_id=getattr(launch, "task_id", None),
                backlog_id=getattr(launch, "backlog_id", None),
                severity="info",
                correlation_id=base_correlation_id,
                causation_id=launch_import_event.event_id,
                payload={
                    "evidence_id": evidence_id,
                    "source_path": ref,
                    "source": "context_engine",
                    "agent": actor,
                    "source_idempotency_key": _idem_key(
                        "context_launch_evidence", project_id, launch_id, ref
                    ),
                },
            ))

        # ── Promotion event ────────────────────────────────────────────────
        promotion_state = getattr(launch, "promotion_state", None)
        if promotion_state:
            sequence += 1
            promotion_id = "promo_" + launch_id[-8:] if launch_id else m.new_promotion_id()
            events.append(m.TelemetryEvent(
                event_id=m.new_telemetry_event_id(),
                event_type="promotion_requested",
                project_id=project_id,
                launch_id=launch_id,
                task_id=getattr(launch, "task_id", None),
                backlog_id=getattr(launch, "backlog_id", None),
                severity="info",
                correlation_id=base_correlation_id,
                causation_id=launch_import_event.event_id,
                payload={
                    "promotion_id": promotion_id,
                    "promotion_state": _value_text(promotion_state),
                    "source": "context_engine",
                    "agent": actor,
                    "source_idempotency_key": _idem_key(
                        "context_launch_promotion", project_id, launch_id,
                        _value_text(promotion_state)
                    ),
                },
            ))

        # ── Summary context_ingested event ────────────────────────────────
        sequence += 1
        events.append(m.TelemetryEvent(
            event_id=m.new_telemetry_event_id(),
            event_type="context_ingested",
            project_id=project_id,
            launch_id=launch_id,
            task_id=getattr(launch, "task_id", None),
            backlog_id=getattr(launch, "backlog_id", None),
            severity="info",
            correlation_id=base_correlation_id,
            causation_id=launch_import_event.event_id,
            payload={
                "source": "context_engine",
                "event_count": sequence,
                "selected_agents": selected_agents,
                "evidence_count": len(evidence_refs),
                "has_promotion": bool(promotion_state),
                "agent": actor,
                "source_idempotency_key": _idem_key(
                    "context_launch_summary", project_id, launch_id,
                    launch_status, launch_stage, launch_updated_at,
                ),
            },
        ))

        return events

    def translate_record_to_events(
        self,
        record: Any,
        launch_id: Optional[str] = None,
        actor: str = "context_engine",
    ) -> List[m.TelemetryEvent]:
        """Translate a context engine ContextRecord into backlog telemetry events.

        Args:
            record: A ContextRecord-like object (duck-typed).
            launch_id: Optional launch_id to associate with the events.
            actor: Actor label for provenance.

        Returns:
            List of TelemetryEvent instances.
        """
        project_id = _required_attr(record, "project_id", "record")
        record_id = _required_attr(record, "record_id", "record")
        backlog_id = f"bkit_{record_id[-8:]}" if record_id else m.new_backlog_id()
        record_updated_at = getattr(record, "updated_at", None)

        events: List[m.TelemetryEvent] = []
        sequence = 0

        sequence += 1
        events.append(m.TelemetryEvent(
            event_id=m.new_telemetry_event_id(),
            event_type="backlog_item_created",
            project_id=project_id,
            launch_id=launch_id,
            backlog_id=backlog_id,
            severity="info",
            correlation_id=None,
            causation_id=None,
            payload={
                "source": "context_engine",
                "record_id": record_id,
                "title": getattr(record, "title", ""),
                "record_type": _value_text(getattr(record, "record_type", "")),
                "body": getattr(record, "body", None),
                "agent": actor,
                "source_idempotency_key": _idem_key(
                    "context_record", project_id, record_id, record_updated_at
                ),
            },
        ))

        return events
