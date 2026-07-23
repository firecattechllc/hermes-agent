"""Conservative Step 33 discovery configuration."""

from pathlib import Path
from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hermes_cli.hermes_link.models import NodeRole, clean_identifier


DEFAULT_COLLECTORS = (
    "host",
    "storage",
    "network",
    "services",
    "docker",
    "ollama",
    "git",
    "scheduled_jobs",
    "python",
    "hermes_runtime",
    "backups",
    "registries",
)


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled_collectors: Tuple[str, ...] = DEFAULT_COLLECTORS
    approved_repository_roots: Tuple[Path, ...] = ()
    approved_environment_roots: Tuple[Path, ...] = ()
    approved_backup_roots: Tuple[Path, ...] = ()
    collector_timeout_seconds: int = Field(default=10, ge=1, le=120)
    maximum_output_bytes: int = Field(default=65536, ge=1024, le=1048576)
    missed_snapshot_threshold: int = Field(default=3, ge=2, le=100)
    traversal_depth: int = Field(default=5, ge=1, le=20)
    database_path: Path = Path("~/.hermes/knowledge/graph.sqlite3").expanduser()
    node_id: str = "mac-hermes"
    node_role: NodeRole = NodeRole.BIG_SISTER
    federation_batch_limit: int = Field(default=100, ge=1, le=500)
    redact_address_hosts: bool = True

    @field_validator("node_id")
    @classmethod
    def valid_node(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator(
        "approved_repository_roots",
        "approved_environment_roots",
        "approved_backup_roots",
    )
    @classmethod
    def absolute_roots(cls, values: Tuple[Path, ...]) -> Tuple[Path, ...]:
        roots = tuple(sorted({path.expanduser().resolve() for path in values}))
        if any(not path.is_absolute() for path in roots):
            raise ValueError("approved discovery roots must be absolute")
        return roots
