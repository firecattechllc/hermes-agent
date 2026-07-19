"""Shared Engineering Context to Structured Engineering Memory adapter.

This module intentionally does not import context_engine models. Context
records are accepted through duck typing, matching the existing Mission
Control adapter convention and avoiding cross-package import coupling.

Every imported record becomes a candidate memory. Verification remains an
explicit EngineeringMemoryService governance action.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Iterable, Optional, Sequence

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.service import EngineeringMemoryService

logger = logging.getLogger("hermes.engineering_memory.adapters.context")


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
    """Resolve the first supported enum value without assuming every schema."""
    for value in preferred_values:
        try:
            return enum_class(value)
        except ValueError:
            continue

    members = list(enum_class)
    if not members:
        raise ValueError(f"{enum_class.__name__} defines no values")

    return members[0]


def _memory_type_for_record(record_type: str) -> m.MemoryType:
    mapping = {
        "architecture_decision": (
            "architecture_decision",
            "implementation_lesson",
        ),
        "engineering_lesson": (
            "implementation_lesson",
            "engineering_lesson",
        ),
        "known_risk": (
            "known_risk",
            "implementation_lesson",
        ),
        "blocker": (
            "failure_pattern",
            "known_risk",
            "implementation_lesson",
        ),
        "operating_constraint": (
            "operating_constraint",
            "architecture_decision",
            "implementation_lesson",
        ),
        "project_fact": (
            "project_fact",
            "implementation_lesson",
        ),
        "objective": (
            "project_fact",
            "architecture_decision",
            "implementation_lesson",
        ),
        "roadmap_item": (
            "project_fact",
            "architecture_decision",
            "implementation_lesson",
        ),
    }

    preferred = mapping.get(
        record_type,
        ("implementation_lesson",),
    )
    return _resolve_enum_value(m.MemoryType, preferred)


def _context_source_type() -> m.MemorySourceType:
    return _resolve_enum_value(
        m.MemorySourceType,
        (
            "context_engine",
            "shared_context",
            "system",
            "human",
        ),
    )


class ContextMemoryAdapter:
    """Translate Shared Engineering Context records into candidate memories."""

    def ingest_record(
        self,
        service: EngineeringMemoryService,
        record: Any,
        *,
        actor: str = "context_engine",
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> m.MemoryRecord:
        project_id = str(
            _required_attr(record, "project_id", "context record")
        )
        record_id = str(
            _required_attr(record, "record_id", "context record")
        )
        title = str(
            _required_attr(record, "title", "context record")
        )

        record_type = _value_text(
            getattr(record, "record_type", "")
        )
        record_status = _value_text(
            getattr(record, "status", "")
        )

        body = getattr(record, "body", None)
        summary = (
            str(body).strip()
            if body not in (None, "")
            else title
        )

        updated_at = getattr(record, "updated_at", None)
        created_at = getattr(record, "created_at", None)
        captured_at = updated_at or created_at or m._utc_now()

        source_refs = getattr(record, "source_refs", None) or []
        evidence_refs: list[str] = []

        for source_ref in source_refs:
            identifier = getattr(
                source_ref,
                "source_identifier",
                None,
            )
            if identifier not in (None, ""):
                evidence_refs.append(str(identifier))

        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}

        tags = _string_list(metadata.get("tags"))
        tags.extend(
            [
                "shared-context",
                record_type or "context-record",
            ]
        )

        structured_payload: Dict[str, Any] = {
            "source_domain": "context_engine",
            "source_record_id": record_id,
            "source_record_type": record_type,
            "source_record_status": record_status,
            "source_metadata": metadata,
        }

        supersedes = _string_list(
            getattr(record, "supersedes", None)
        )
        if supersedes:
            structured_payload["source_supersedes"] = supersedes

        provenance = m.MemoryProvenance(
            source_type=_context_source_type(),
            source_ids=(f"context_record:{record_id}",),
            evidence_refs=tuple(evidence_refs),
            captured_at=int(captured_at),
            captured_by=actor,
        )

        source_key = ":".join(
            [
                "context_memory",
                project_id,
                record_id,
                str(updated_at or created_at or ""),
            ]
        )

        memory = service.create_candidate(
            project_id,
            _memory_type_for_record(record_type),
            title,
            summary,
            provenance=provenance,
            body=str(body) if body is not None else None,
            structured_payload=structured_payload,
            tags=tags,
            created_by=actor,
            actor=actor,
            memory_id=_stable_memory_id(
                "mem",
                "context",
                project_id,
                record_id,
            ),
            source_idempotency_key=source_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        if memory.status != m.MemoryStatus.CANDIDATE:
            raise ValueError(
                "context adapter may only produce candidate memories"
            )

        logger.info(
            "ingested context record %s as candidate memory %s "
            "for project %s",
            record_id,
            memory.memory_id,
            project_id,
        )
        return memory

    def ingest_records(
        self,
        service: EngineeringMemoryService,
        records: Iterable[Any],
        *,
        actor: str = "context_engine",
        correlation_id: Optional[str] = None,
    ) -> list[m.MemoryRecord]:
        memories: list[m.MemoryRecord] = []

        for record in records:
            memories.append(
                self.ingest_record(
                    service,
                    record,
                    actor=actor,
                    correlation_id=correlation_id,
                )
            )

        return memories
