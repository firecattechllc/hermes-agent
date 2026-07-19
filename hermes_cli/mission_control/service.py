"""Application-layer service for Hermes Mission Control telemetry.

This layer contains all business logic, sequence assignment, idempotent context
ingestion, and the public API used by CLI commands and the telemetry dashboard.
The store layer handles only persistence; this layer handles coordination and
deduplication.

No UI or write-action API is exposed directly — the service is read-only from
the consumer perspective. All telemetry data arrives through ``append_event()``
or ``ingest_context_launch()``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_cli.mission_control import models as m
from hermes_cli.mission_control.adapters.context_adapter import ContextAdapter
from hermes_cli.mission_control.store import MissionControlStore, get_store

logger = logging.getLogger("hermes.mission_control.service")

# ── Service class ────────────────────────────────────────────────────────────

class MissionControlService:
    """Application service for Hermes Mission Control.

    All methods return domain objects (or raise typed exceptions). The store
    layer is the only persistence interface; this layer adds sequence
    assignment, deduplication, and cross-domain translation.

    Args:
        store: Explicit store instance. Defaults to the global store.
               Intended for tests and multi-store scenarios.
        adapter: ContextAdapter for translating context_engine models.
                 Defaults to a new ContextAdapter instance.
    """

    def __init__(
        self,
        store: Optional[MissionControlStore] = None,
        adapter: Optional[ContextAdapter] = None,
    ) -> None:
        self._store = store or get_store()
        self._adapter = adapter or ContextAdapter()

    # ── Event ingestion ─────────────────────────────────────────────────────

    def append_event(
        self,
        event: m.TelemetryEvent,
    ) -> m.TelemetryEvent:
        """Append a single telemetry event.

        If ``event.sequence`` is 0, an auto-incrementing sequence number is
        assigned based on the current event count for the project.

        Returns the event (with sequence populated if it was zero).
        """
        with self._store.write_lock():
            if event.sequence == 0:
                count = self._store.event_count(project_id=event.project_id)
                event.sequence = count + 1

            self._store._append_event_unlocked(event)
        logger.debug(
            "appended telemetry event %s (type=%s, project=%s, seq=%d)",
            event.event_id,
            event.event_type,
            event.project_id,
            event.sequence,
        )
        return event

    def append_events(
        self,
        events: List[m.TelemetryEvent],
    ) -> List[m.TelemetryEvent]:
        """Append multiple telemetry events in a single batch.

        Sequence numbers are auto-assigned sequentially for each project.
        """
        if not events:
            return events

        with self._store.write_lock():
            # Collect per-project sequence offsets.
            project_counts: Dict[str, int] = {}
            for event in events:
                if event.sequence == 0:
                    pid = event.project_id
                    if pid not in project_counts:
                        project_counts[pid] = self._store.event_count(project_id=pid)

            # Assign sequences.
            for event in events:
                if event.sequence == 0:
                    pid = event.project_id
                    project_counts[pid] += 1
                    event.sequence = project_counts[pid]

            self._store._append_events_unlocked(events)
        logger.debug("appended %d telemetry events in batch", len(events))
        return events

    # ── Snapshot and query ──────────────────────────────────────────────────

    def get_snapshot(
        self,
        project_id: str,
        *,
        generated_by: Optional[str] = None,
    ) -> m.MissionControlSnapshot:
        """Build a deterministic point-in-time snapshot for a project."""
        return self._store.build_snapshot(
            project_id,
            generated_by=generated_by,
        )

    def get_events(
        self,
        project_id: str,
    ) -> List[m.TelemetryEvent]:
        """Return all telemetry events for a project (sorted)."""
        events = list(self._store.iter_events(project_id=project_id))
        events.sort(key=lambda e: e.stable_sort_key())
        return events

    def event_count(self, project_id: str) -> int:
        """Return the event count for a project."""
        return self._store.event_count(project_id=project_id)

    def list_project_ids(self) -> List[str]:
        """List project IDs that have telemetry data."""
        return self._store.list_project_ids()

    # ── Idempotent context ingestion ────────────────────────────────────────

    def ingest_context_launch(
        self,
        launch: Any,
        project: Optional[Any] = None,
        records: Optional[List[Any]] = None,
        actor: str = "context_engine",
    ) -> List[m.TelemetryEvent]:
        """Ingest context engine launch state as telemetry events.

        Idempotent: if events already exist for this ``launch.launch_id`` in
        the project's journal, no duplicate events are created. Returns the
        list of events that were appended (or an empty list if already ingested).

        The ``launch`` parameter is a ``hermes_cli.context_engine.models.LaunchContext``
        (or any object with ``launch_id``, ``project_id``, ``status``, ``stage``,
        ``task_id``, ``backlog_id``, ``selected_agents``, ``evidence_refs``,
        ``commits``, ``branches``, ``pull_request_urls``, ``promotion_state``,
        and ``failure_reason`` attributes, duck-typing supported).

        Args:
            launch: The launch context to ingest.
            project: Optional project metadata (for ``project_registered`` event).
            records: Optional list of context records to ingest as backlog items.
            actor: Actor label for provenance.

        Returns:
            List of TelemetryEvent instances that were persisted.
        """
        project_id = launch.project_id
        launch_id = launch.launch_id

        events = self._adapter.translate_launch_to_events(launch, project=project, actor=actor)
        if records:
            for record in records:
                events.extend(
                    self._adapter.translate_record_to_events(
                        record,
                        launch_id=launch_id,
                        actor=actor,
                    )
                )

        existing_keys = {
            existing.payload.get("source_idempotency_key")
            for existing in self._store.iter_events(project_id=project_id)
            if existing.payload.get("source_idempotency_key")
        }
        new_events = [
            event
            for event in events
            if event.payload.get("source_idempotency_key") not in existing_keys
        ]
        if not new_events:
            logger.info(
                "context launch %s already ingested for project %s, skipping",
                launch_id,
                project_id,
            )
            return []

        self.append_events(new_events)
        logger.info(
            "ingested context launch %s (%d events) for project %s",
            launch_id,
            len(new_events),
            project_id,
        )
        return new_events
