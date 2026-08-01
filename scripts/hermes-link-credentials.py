#!/usr/bin/env python3
"""Initialize, rotate, revoke, and verify external Hermes Link credentials."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from hermes_cli.hermes_link.security import (
    CredentialEvidenceStore,
    CredentialRegistry,
    CredentialStatus,
    SigningCredential,
    generate_secret,
    resolve_secret,
)


def _paths(root: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("credential root must be an absolute non-symlink path")
    return root / "credentials.json", root / "secrets"


def _record(root: Path, action: str, item: SigningCredential, *, now: int) -> None:
    CredentialEvidenceStore(root / "credential-evidence.jsonl").append(
        action,
        item.credential_id,
        item.coordinator_node_id,
        item.target_node_id,
        recorded_at=now,
    )


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(data.encode())
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("credential write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_registry(path: Path, registry: CredentialRegistry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".json.new")
    if temporary.exists():
        raise ValueError("credential registry staging file already exists")
    _write(temporary, registry.model_dump_json(indent=2) + "\n")
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _credential(
    args: argparse.Namespace, secret_path: Path, *, now: int
) -> SigningCredential:
    return SigningCredential(
        credential_id=args.credential_id,
        secret_reference=f"file:{secret_path}",
        coordinator_node_id=args.coordinator_node_id,
        target_node_id=args.target_node_id,
        status=CredentialStatus.ACTIVE,
        not_before=now,
        expires_at=now + args.ttl_seconds,
    )


def initialize(args: argparse.Namespace) -> dict[str, object]:
    registry_path, secret_root = _paths(args.root)
    if registry_path.exists():
        raise ValueError("credential registry already exists")
    now = int(time.time())
    secret_path = secret_root / f"{args.credential_id}.secret"
    _write(secret_path, generate_secret() + "\n")
    value = _credential(args, secret_path, now=now)
    _replace_registry(registry_path, CredentialRegistry(credentials=(value,)))
    _record(args.root, "credential_enrolled", value, now=now)
    return _report("initialized", value, registry_path)


def enroll(args: argparse.Namespace) -> dict[str, object]:
    registry_path, secret_root = _paths(args.root)
    registry = CredentialRegistry.load(registry_path)
    now = int(time.time())
    if any(
        item.coordinator_node_id == args.coordinator_node_id
        and item.target_node_id == args.target_node_id
        and item.status == CredentialStatus.ACTIVE
        for item in registry.credentials
    ):
        raise ValueError("node pair already has an active credential")
    secret_path = secret_root / f"{args.credential_id}.secret"
    _write(secret_path, generate_secret() + "\n")
    value = _credential(args, secret_path, now=now)
    _replace_registry(
        registry_path,
        CredentialRegistry(credentials=registry.credentials + (value,)),
    )
    _record(args.root, "credential_enrolled", value, now=now)
    return _report("enrolled", value, registry_path)


def rotate(args: argparse.Namespace) -> dict[str, object]:
    registry_path, secret_root = _paths(args.root)
    registry = CredentialRegistry.load(registry_path)
    now = int(time.time())
    active = registry.active_for(args.coordinator_node_id, args.target_node_id, now=now)
    if args.credential_id == active.credential_id:
        raise ValueError("new credential identity must differ from the active identity")
    retiring = active.model_copy(
        update={
            "status": CredentialStatus.RETIRING,
            "expires_at": min(active.expires_at, now + args.overlap_seconds),
        }
    )
    secret_path = secret_root / f"{args.credential_id}.secret"
    _write(secret_path, generate_secret() + "\n")
    replacement = _credential(args, secret_path, now=now)
    values = tuple(
        retiring if item.credential_id == active.credential_id else item
        for item in registry.credentials
    ) + (replacement,)
    _replace_registry(registry_path, CredentialRegistry(credentials=values))
    _record(args.root, "credential_rotated", replacement, now=now)
    return _report(
        "rotated", replacement, registry_path, retiring=retiring.credential_id
    )


def revoke(args: argparse.Namespace) -> dict[str, object]:
    registry_path, _ = _paths(args.root)
    registry = CredentialRegistry.load(registry_path)
    found = False
    values = []
    for item in registry.credentials:
        if item.credential_id == args.credential_id:
            found = True
            values.append(item.model_copy(update={"status": CredentialStatus.REVOKED}))
        else:
            values.append(item)
    if not found:
        raise ValueError("credential identity is not enrolled")
    _replace_registry(registry_path, CredentialRegistry(credentials=tuple(values)))
    item = next(value for value in values if value.credential_id == args.credential_id)
    _record(args.root, "credential_revoked", item, now=int(time.time()))
    return _report("revoked", item, registry_path)


def verify(args: argparse.Namespace) -> dict[str, object]:
    registry_path, _ = _paths(args.root)
    registry = CredentialRegistry.load(registry_path)
    now = int(time.time())
    for item in registry.credentials:
        resolve_secret(item.secret_reference)
    active = [item for item in registry.credentials if item.usable(now=now)]
    for item in active:
        _record(args.root, "enrollment_verified", item, now=now)
    return {
        "ok": True,
        "action": "verified",
        "registry_reference": "configured",
        "credential_count": len(registry.credentials),
        "usable_credential_ids": sorted(item.credential_id for item in active),
        "secret_material": "not_displayed",
    }


def _report(
    action: str,
    item: SigningCredential,
    registry_path: Path,
    *,
    retiring: str | None = None,
) -> dict[str, object]:
    return {
        "ok": True,
        "action": action,
        "credential_id": item.credential_id,
        "coordinator_node_id": item.coordinator_node_id,
        "target_node_id": item.target_node_id,
        "status": item.status,
        "expires_at": item.expires_at,
        "retiring_credential_id": retiring,
        "registry_reference": "configured",
        "secret_material": "not_displayed",
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Manage signed Hermes Link credentials")
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("initialize", "enroll", "rotate"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        command.add_argument("--credential-id", required=True)
        command.add_argument("--coordinator-node-id", required=True)
        command.add_argument("--target-node-id", required=True)
        command.add_argument("--ttl-seconds", type=int, default=2_592_000)
        if name == "rotate":
            command.add_argument("--overlap-seconds", type=int, default=3600)
    revoke_command = commands.add_parser("revoke")
    revoke_command.add_argument("--root", required=True, type=Path)
    revoke_command.add_argument("--credential-id", required=True)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--root", required=True, type=Path)
    return value


def main() -> int:
    arguments = parser().parse_args()
    handlers = {
        "initialize": initialize,
        "enroll": enroll,
        "rotate": rotate,
        "revoke": revoke,
        "verify": verify,
    }
    try:
        report = handlers[arguments.command](arguments)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
