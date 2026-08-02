"""Backend-only Mac coordinator configuration for signed fleet nodes."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .client import HermesLinkClient
from .models import clean_identifier
from .security import CredentialRegistry


class CoordinatorTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: str
    authenticated_identity_ref: str
    tailnet_dns_identity: str
    base_url: str

    @field_validator("node_id")
    @classmethod
    def node_identifier(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("authenticated_identity_ref")
    @classmethod
    def identity_reference(cls, value: str) -> str:
        if not value.startswith("tailnet-node:") or len(value) > 180:
            raise ValueError(
                "target identity must use a sanitized tailnet-node reference"
            )
        return value

    @field_validator("tailnet_dns_identity")
    @classmethod
    def tailnet_dns(cls, value: str) -> str:
        value = value.rstrip(".").lower()
        if not value.endswith(".ts.net") or "/" in value or ":" in value:
            raise ValueError("target DNS identity must be a tailnet DNS name")
        return value

    @field_validator("base_url")
    @classmethod
    def backend_loopback_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("coordinator targets must use a reviewed local tunnel")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("coordinator target URL is invalid")
        return value.rstrip("/")


class MacCoordinatorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    coordinator_node_id: str = "mac-hermes"
    credential_registry_path: Path
    targets: tuple[CoordinatorTarget, ...] = ()

    @field_validator("coordinator_node_id")
    @classmethod
    def node_identifier(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("credential_registry_path")
    @classmethod
    def absolute_registry(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("coordinator credential registry path must be absolute")
        return value

    @model_validator(mode="after")
    def unique_targets(self) -> "MacCoordinatorConfig":
        ids = [item.node_id for item in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("coordinator target identities must be unique")
        return self

    @classmethod
    def load(cls, path: Path) -> "MacCoordinatorConfig":
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("coordinator configuration permissions must be 0600")
        return cls.model_validate_json(path.read_bytes())

    def clients(self) -> dict[str, HermesLinkClient]:
        if not self.enabled:
            return {}
        registry = CredentialRegistry.load(self.credential_registry_path)
        return {
            target.node_id: HermesLinkClient(
                target.base_url,
                credential_registry=registry,
                coordinator_node_id=self.coordinator_node_id,
                target_node_id=target.node_id,
            )
            for target in self.targets
        }

    def sanitized_status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "coordinator_node_id": self.coordinator_node_id,
            "targets": tuple(
                {
                    "node_id": item.node_id,
                    "authenticated_identity_ref": item.authenticated_identity_ref,
                    "tailnet_dns_identity": item.tailnet_dns_identity,
                    "configured": True,
                }
                for item in self.targets
            ),
            "credential_material": "not_displayed",
            "paper_only": True,
            "broker_submission": False,
        }
