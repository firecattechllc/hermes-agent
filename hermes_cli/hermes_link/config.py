"""Explicit, secret-reference-only Hermes-link configuration."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import NodeRole, clean_identifier


class HermesLinkConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    node_id: str = "mac-hermes"
    node_role: NodeRole = NodeRole.BIG_SISTER
    titan_base_url: str = "http://127.0.0.1:9320"
    bind_host: str = "127.0.0.1"
    authentication_provider: str = "bearer_env"
    authentication_token_reference: Optional[str] = "env:HERMES_LINK_TOKEN"
    connect_timeout_seconds: float = Field(default=2.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    queue_path: Path = Path("~/.hermes/link").expanduser()
    maximum_retries: int = Field(default=3, ge=1, le=20)
    maximum_payload_bytes: int = Field(default=65536, ge=1024, le=1048576)

    @field_validator("node_id")
    @classmethod
    def node_identifier(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("bind_host")
    @classmethod
    def private_bind(cls, value: str) -> str:
        if value not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError(
                "Hermes-link binds to loopback by default; explicit deployment review is required"
            )
        return value

    @field_validator("authentication_token_reference")
    @classmethod
    def token_reference(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.startswith(("env:", "file:")):
            raise ValueError(
                "authentication token must be an env: or file: reference, never inline"
            )
        return value

    @model_validator(mode="after")
    def auth_required_when_enabled(self) -> "HermesLinkConfig":
        if self.enabled and not self.authentication_token_reference:
            raise ValueError("enabled Hermes-link requires application authentication")
        return self
