"""Cross-domain adapters for Hermes Structured Engineering Memory.

Adapters translate source-domain objects into candidate memories without
importing source packages at module load time. They never verify memories.
"""

from hermes_cli.engineering_memory.adapters.context_adapter import (
    ContextMemoryAdapter,
)
from hermes_cli.engineering_memory.adapters.mission_control_adapter import (
    MissionControlMemoryAdapter,
)

__all__ = [
    "ContextMemoryAdapter",
    "MissionControlMemoryAdapter",
]
