"""Restricted signed Hermes Link service entrypoint for operator-managed nodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .api import create_app
from .models import NodeRole, clean_identifier
from .security import CredentialRegistry, DurableReplayStore, SignedRequestAuthenticator
from .service import HermesLinkService
from .store import HermesLinkStore


class SignedServiceConfig(BaseModel):
    """Secret-free service configuration; credentials are external references."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    local_node_id: str
    coordinator_node_id: str
    node_role: NodeRole = NodeRole.LITTLE_SISTER
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=9320, ge=1024, le=65535)
    state_root: Path
    credential_registry_path: Path
    maximum_payload_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)
    maximum_clock_skew_seconds: int = Field(default=120, ge=30, le=300)
    maximum_task_timeout_ms: int = Field(default=30_000, ge=100, le=300_000)
    maximum_output_chars: int = Field(default=8_192, ge=128, le=16_384)
    maximum_concurrency: int = Field(default=1, ge=1, le=4)
    network_allowed: bool = False
    shell_allowed: bool = False
    filesystem_allowed: bool = False
    credentials_available_to_tasks: bool = False
    broker_available: bool = False
    portfolio_available: bool = False
    recursive_workers_allowed: bool = False
    worker_task_types: tuple[str, ...] = ()

    @field_validator("local_node_id", "coordinator_node_id")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("bind_host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("signed Hermes Link service binds to loopback only")
        return value

    @field_validator("state_root", "credential_registry_path")
    @classmethod
    def absolute_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("service paths must be absolute")
        return value

    def verify_no_authority(self) -> None:
        if any((
            self.network_allowed,
            self.shell_allowed,
            self.filesystem_allowed,
            self.credentials_available_to_tasks,
            self.broker_available,
            self.portfolio_available,
            self.recursive_workers_allowed,
        )):
            raise ValueError(
                "signed Hermes Link service configuration requests prohibited authority"
            )

    @classmethod
    def load(cls, path: Path) -> "SignedServiceConfig":
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o022:
            raise ValueError(
                "service configuration is missing or writable by untrusted users"
            )
        value = cls.model_validate_json(path.read_bytes())
        value.verify_no_authority()
        return value


def build_app(config: SignedServiceConfig):
    config.verify_no_authority()
    config.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    registry = CredentialRegistry.load(config.credential_registry_path)
    if not any(
        item.coordinator_node_id == config.coordinator_node_id
        and item.target_node_id == config.local_node_id
        for item in registry.credentials
    ):
        raise ValueError(
            "service credential registry does not bind the configured nodes"
        )
    service = HermesLinkService(
        HermesLinkStore(config.state_root / "messages"),
        local_node=config.local_node_id,
        peer_node=config.coordinator_node_id,
        node_role=config.node_role,
        maximum_payload_bytes=config.maximum_payload_bytes,
    )
    authenticator = SignedRequestAuthenticator(
        registry,
        DurableReplayStore(config.state_root / "transport-evidence.jsonl"),
        target_node_id=config.local_node_id,
        maximum_clock_skew_seconds=config.maximum_clock_skew_seconds,
    )
    app = create_app(service, signed_authenticator=authenticator)
    if config.worker_task_types:
        from .remote_worker import (
            DurableRemoteTaskStore,
            GovernedRemoteWorkerService,
            attach_remote_worker_routes,
            digest_evidence_handler,
        )

        if not set(config.worker_task_types).issubset({
            "research_preparation",
            "deterministic_calculation",
        }):
            raise ValueError("remote worker task allowlist is unsupported")
        worker = GovernedRemoteWorkerService(
            config.local_node_id,
            DurableRemoteTaskStore(config.state_root / "remote-tasks.json"),
            {item: digest_evidence_handler for item in config.worker_task_types},
            maximum_timeout_ms=config.maximum_task_timeout_ms,
            maximum_output_chars=config.maximum_output_chars,
            maximum_concurrency=config.maximum_concurrency,
        )
        attach_remote_worker_routes(app, worker, authenticator)
    return app


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run a restricted signed Hermes Link service"
    )
    value.add_argument("--config", required=True, type=Path)
    value.add_argument("--validate", action="store_true")
    return value


def main() -> int:
    arguments = parser().parse_args()
    config = SignedServiceConfig.load(arguments.config)
    build_app(config)
    if arguments.validate:
        print(
            json.dumps(
                {
                    "ok": True,
                    "node_id": config.local_node_id,
                    "authentication": "signed_hmac_sha256",
                    "credential_references": "available",
                    "paper_only": True,
                    "broker_submission": False,
                    "shell": False,
                    "network_for_tasks": False,
                },
                sort_keys=True,
            )
        )
        return 0
    import uvicorn

    uvicorn.run(
        build_app(config),
        host=config.bind_host,
        port=config.bind_port,
        access_log=False,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
