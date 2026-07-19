"""Hermes Structured Engineering Memory.

This package provides project-scoped, governed, durable engineering knowledge.
Shared Engineering Context remains the canonical current-context source.
Mission Control remains the canonical operational telemetry source.
"""

from hermes_cli.engineering_memory.models import (
    CURRENT_SCHEMA_VERSION,
    MemoryEvent,
    MemoryEventType,
    MemoryProvenance,
    MemoryRecord,
    MemorySnapshot,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
    new_memory_event_id,
    new_memory_id,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MemoryEvent",
    "MemoryEventType",
    "MemoryProvenance",
    "MemoryRecord",
    "MemorySnapshot",
    "MemorySourceType",
    "MemoryStatus",
    "MemoryType",
    "new_memory_event_id",
    "new_memory_id",
]

from hermes_cli.engineering_memory.store import (
    EngineeringMemoryStore,
    engineering_memory_root,
    get_store,
)

__all__ += [
    "EngineeringMemoryStore",
    "engineering_memory_root",
    "get_store",
]

from hermes_cli.engineering_memory.service import EngineeringMemoryService

__all__ += [
    "EngineeringMemoryService",
]

from hermes_cli.engineering_memory.adapters import (
    ContextMemoryAdapter,
    MissionControlMemoryAdapter,
)

__all__ += [
    "ContextMemoryAdapter",
    "MissionControlMemoryAdapter",
]
