"""Application service for Hermes Structured Engineering Memory.

The service owns lifecycle governance and business rules. The store owns only
durable event persistence and deterministic replay.

Supported lifecycle:

    candidate -> verified
    candidate -> rejected
    candidate/verified -> superseded
    candidate/verified/rejected -> archived

Historical events are append-only. Every transition writes the full resulting
MemoryRecord into the event payload so state remains replayable without hidden
mutable files.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.store import (
    EngineeringMemoryStore,
    get_store,
)

logger = logging.getLogger("hermes.engineering_memory.service")


class EngineeringMemoryService:
    """Governed application service for Structured Engineering Memory."""

    def __init__(
        self,
        store: Optional[EngineeringMemoryStore] = None,
    ) -> None:
        self._store = store or get_store()

    # ── Internal helpers ───────────────────────────────────────────────────

    def _require_memory(
        self,
        project_id: str,
        memory_id: str,
    ) -> m.MemoryRecord:
        memory = self._store.get_memory(project_id, memory_id)
        if memory is None:
            raise ValueError(
                f"no such memory {memory_id!r} in project {project_id!r}"
            )
        return memory

    def _event_for(
        self,
        memory: m.MemoryRecord,
        *,
        event_type: m.MemoryEventType,
        actor: Optional[str],
        source_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> m.MemoryEvent:
        payload: Dict[str, Any] = {
            "memory": memory.model_dump(mode="json"),
            "content_fingerprint": memory.content_fingerprint(),
        }

        if source_idempotency_key is not None:
            payload["source_idempotency_key"] = source_idempotency_key

        if extra_payload:
            payload.update(extra_payload)

        return m.MemoryEvent(
            event_id=m.new_memory_event_id(),
            event_type=event_type,
            project_id=memory.project_id,
            memory_id=memory.memory_id,
            actor=actor,
            timestamp=m._utc_now(),
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload,
        )

    def _find_duplicate(
        self,
        project_id: str,
        fingerprint: str,
    ) -> Optional[m.MemoryRecord]:
        for memory in self._store.list_memories(
            project_id,
            include_inactive=True,
        ):
            if memory.content_fingerprint() == fingerprint:
                return memory
        return None

    # ── Creation ───────────────────────────────────────────────────────────

    def create_candidate(
        self,
        project_id: str,
        memory_type: m.MemoryType,
        title: str,
        summary: str,
        *,
        provenance: m.MemoryProvenance,
        body: Optional[str] = None,
        structured_payload: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        tags: Optional[List[str]] = None,
        related_memory_ids: Optional[List[str]] = None,
        created_by: Optional[str] = None,
        actor: Optional[str] = None,
        memory_id: Optional[str] = None,
        source_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> m.MemoryRecord:
        """Create a candidate memory.

        Semantically identical content is idempotent: if a record with the same
        content fingerprint already exists in the project, the existing record
        is returned rather than creating a duplicate.

        ``source_idempotency_key`` also protects ingestion pipelines against
        repeated source delivery.
        """
        now = m._utc_now()

        candidate = m.MemoryRecord(
            memory_id=memory_id or m.new_memory_id(),
            project_id=project_id,
            memory_type=memory_type,
            title=title,
            summary=summary,
            body=body,
            structured_payload=structured_payload,
            status=m.MemoryStatus.CANDIDATE,
            confidence=confidence,
            provenance=provenance,
            tags=tags or [],
            related_memory_ids=related_memory_ids or [],
            created_at=now,
            updated_at=now,
            created_by=created_by or actor,
        )

        duplicate = self._find_duplicate(
            project_id,
            candidate.content_fingerprint(),
        )
        if duplicate is not None:
            logger.debug(
                "duplicate memory candidate detected for project %s: %s",
                project_id,
                duplicate.memory_id,
            )
            return duplicate

        event = self._event_for(
            candidate,
            event_type=m.MemoryEventType.CREATED,
            actor=actor,
            source_idempotency_key=source_idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        appended = self._store.append_event_once(event)
        if appended is None and source_idempotency_key is not None:
            for existing_event in self._store.iter_events(project_id=project_id):
                if (
                    existing_event.payload.get("source_idempotency_key")
                    == source_idempotency_key
                ):
                    existing = self._store.get_memory(
                        project_id,
                        existing_event.memory_id,
                    )
                    if existing is not None:
                        return existing

            raise ValueError(
                "idempotency key exists but corresponding memory "
                "could not be reconstructed"
            )

        logger.info(
            "created candidate memory %s for project %s",
            candidate.memory_id,
            project_id,
        )
        return candidate

    # ── Reads ──────────────────────────────────────────────────────────────

    def get_memory(
        self,
        project_id: str,
        memory_id: str,
    ) -> Optional[m.MemoryRecord]:
        return self._store.get_memory(project_id, memory_id)

    def list_memories(
        self,
        project_id: str,
        *,
        status: Optional[m.MemoryStatus] = None,
        memory_type: Optional[m.MemoryType] = None,
        include_inactive: bool = False,
    ) -> List[m.MemoryRecord]:
        return self._store.list_memories(
            project_id,
            status=status,
            memory_type=memory_type,
            include_inactive=include_inactive,
        )

    def build_snapshot(
        self,
        project_id: str,
        *,
        generated_by: Optional[str] = None,
    ) -> m.MemorySnapshot:
        return self._store.build_snapshot(
            project_id,
            generated_by=generated_by,
        )

    def list_events(
        self,
        project_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
    ) -> List[m.MemoryEvent]:
        """List append-only memory lifecycle events.

        Events may be scoped to one project. A limit returns the newest
        matching events while preserving their original order.
        """
        events = list(
            self._store.iter_events(project_id=project_id)
        )
        if limit is not None:
            events = events[-limit:]
        return events

    # ── Verification ───────────────────────────────────────────────────────

    def verify_memory(
        self,
        project_id: str,
        memory_id: str,
        *,
        reviewed_by: str,
        review_note: Optional[str] = None,
        confidence: Optional[float] = None,
        actor: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> m.MemoryRecord:
        existing = self._require_memory(project_id, memory_id)

        if existing.status == m.MemoryStatus.VERIFIED:
            return existing

        if existing.status != m.MemoryStatus.CANDIDATE:
            raise ValueError(
                f"cannot verify memory {memory_id!r} from "
                f"status {existing.status.value!r}"
            )

        now = m._utc_now()
        data = existing.model_dump()
        data.update(
            {
                "status": m.MemoryStatus.VERIFIED,
                "confidence": (
                    confidence
                    if confidence is not None
                    else existing.confidence
                ),
                "updated_at": now,
                "reviewed_by": reviewed_by,
                "reviewed_at": now,
                "review_note": review_note,
            }
        )
        verified = m.MemoryRecord(**data)

        event = self._event_for(
            verified,
            event_type=m.MemoryEventType.VERIFIED,
            actor=actor or reviewed_by,
            correlation_id=correlation_id,
            causation_id=causation_id,
            extra_payload={
                "previous_status": existing.status.value,
            },
        )
        self._store.append_event(event)

        logger.info(
            "verified memory %s for project %s",
            memory_id,
            project_id,
        )
        return verified

    # ── Rejection ──────────────────────────────────────────────────────────

    def reject_memory(
        self,
        project_id: str,
        memory_id: str,
        *,
        reviewed_by: str,
        review_note: str,
        actor: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> m.MemoryRecord:
        existing = self._require_memory(project_id, memory_id)

        if existing.status == m.MemoryStatus.REJECTED:
            return existing

        if existing.status != m.MemoryStatus.CANDIDATE:
            raise ValueError(
                f"cannot reject memory {memory_id!r} from "
                f"status {existing.status.value!r}"
            )

        now = m._utc_now()
        data = existing.model_dump()
        data.update(
            {
                "status": m.MemoryStatus.REJECTED,
                "updated_at": now,
                "reviewed_by": reviewed_by,
                "reviewed_at": now,
                "review_note": review_note,
            }
        )
        rejected = m.MemoryRecord(**data)

        event = self._event_for(
            rejected,
            event_type=m.MemoryEventType.REJECTED,
            actor=actor or reviewed_by,
            correlation_id=correlation_id,
            causation_id=causation_id,
            extra_payload={
                "previous_status": existing.status.value,
            },
        )
        self._store.append_event(event)

        logger.info(
            "rejected memory %s for project %s",
            memory_id,
            project_id,
        )
        return rejected

    # ── Supersession ───────────────────────────────────────────────────────

    def supersede_memory(
        self,
        project_id: str,
        memory_id: str,
        *,
        replacement_type: m.MemoryType,
        replacement_title: str,
        replacement_summary: str,
        replacement_provenance: m.MemoryProvenance,
        replacement_body: Optional[str] = None,
        replacement_structured_payload: Optional[Dict[str, Any]] = None,
        replacement_confidence: Optional[float] = None,
        replacement_tags: Optional[List[str]] = None,
        replacement_created_by: Optional[str] = None,
        actor: Optional[str] = None,
        replacement_memory_id: Optional[str] = None,
        source_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> m.MemoryRecord:
        """Supersede an existing candidate or verified memory.

        The replacement begins as a candidate and must be independently
        verified. The old memory becomes superseded and links to the new record.
        Both lifecycle events are appended in one locked batch.
        """
        existing = self._require_memory(project_id, memory_id)

        if existing.status == m.MemoryStatus.SUPERSEDED:
            if existing.superseded_by is None:
                raise ValueError(
                    f"superseded memory {memory_id!r} lacks replacement identity"
                )

            replacement = self._store.get_memory(
                project_id,
                existing.superseded_by,
            )
            if replacement is None:
                raise ValueError(
                    f"replacement memory {existing.superseded_by!r} "
                    "could not be reconstructed"
                )
            return replacement

        if existing.status not in {
            m.MemoryStatus.CANDIDATE,
            m.MemoryStatus.VERIFIED,
        }:
            raise ValueError(
                f"cannot supersede memory {memory_id!r} from "
                f"status {existing.status.value!r}"
            )

        now = m._utc_now()
        replacement_id = replacement_memory_id or m.new_memory_id()

        replacement = m.MemoryRecord(
            memory_id=replacement_id,
            project_id=project_id,
            memory_type=replacement_type,
            title=replacement_title,
            summary=replacement_summary,
            body=replacement_body,
            structured_payload=replacement_structured_payload,
            status=m.MemoryStatus.CANDIDATE,
            confidence=replacement_confidence,
            provenance=replacement_provenance,
            tags=replacement_tags or [],
            related_memory_ids=[memory_id],
            created_at=now,
            updated_at=now,
            created_by=replacement_created_by or actor,
            supersedes=[memory_id],
        )

        duplicate = self._find_duplicate(
            project_id,
            replacement.content_fingerprint(),
        )
        if duplicate is not None and duplicate.memory_id != memory_id:
            replacement = duplicate
            replacement_id = duplicate.memory_id

        old_data = existing.model_dump()
        old_data.update(
            {
                "status": m.MemoryStatus.SUPERSEDED,
                "updated_at": now,
                "superseded_by": replacement_id,
            }
        )
        superseded = m.MemoryRecord(**old_data)

        old_event = self._event_for(
            superseded,
            event_type=m.MemoryEventType.SUPERSEDED,
            actor=actor,
            correlation_id=correlation_id,
            causation_id=causation_id,
            extra_payload={
                "previous_status": existing.status.value,
                "replacement_memory_id": replacement_id,
            },
        )

        events = [old_event]

        if duplicate is None:
            replacement_event = self._event_for(
                replacement,
                event_type=m.MemoryEventType.CREATED,
                actor=actor,
                source_idempotency_key=source_idempotency_key,
                correlation_id=correlation_id,
                causation_id=old_event.event_id,
                extra_payload={
                    "supersedes_memory_id": memory_id,
                },
            )
            events.append(replacement_event)

        self._store.append_events(events)

        logger.info(
            "superseded memory %s with %s for project %s",
            memory_id,
            replacement_id,
            project_id,
        )
        return replacement

    # ── Archival ───────────────────────────────────────────────────────────

    def archive_memory(
        self,
        project_id: str,
        memory_id: str,
        *,
        actor: Optional[str] = None,
        archive_note: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> m.MemoryRecord:
        existing = self._require_memory(project_id, memory_id)

        if existing.status == m.MemoryStatus.ARCHIVED:
            return existing

        if existing.status == m.MemoryStatus.SUPERSEDED:
            raise ValueError(
                f"cannot archive superseded memory {memory_id!r}"
            )

        now = m._utc_now()
        data = existing.model_dump()
        data.update(
            {
                "status": m.MemoryStatus.ARCHIVED,
                "updated_at": now,
                "review_note": (
                    archive_note
                    if archive_note is not None
                    else existing.review_note
                ),
            }
        )
        archived = m.MemoryRecord(**data)

        event = self._event_for(
            archived,
            event_type=m.MemoryEventType.ARCHIVED,
            actor=actor,
            correlation_id=correlation_id,
            causation_id=causation_id,
            extra_payload={
                "previous_status": existing.status.value,
            },
        )
        self._store.append_event(event)

        logger.info(
            "archived memory %s for project %s",
            memory_id,
            project_id,
        )
        return archived
