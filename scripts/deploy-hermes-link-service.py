#!/usr/bin/env python3
"""Idempotent installer for the restricted signed Hermes Link systemd service."""

from __future__ import annotations

import argparse
import io
import hashlib
import json
import shlex
import subprocess
import tarfile
from pathlib import Path

from hermes_cli.hermes_link.runtime import SignedServiceConfig
from hermes_cli.hermes_link.security import CredentialRegistry, resolve_secret


UNIT = Path(__file__).parents[1] / "deploy/hermes-link/hermes-link.service"
SOURCE_ROOT = Path(__file__).parents[1]


def _source_files() -> tuple[Path, ...]:
    values = list((SOURCE_ROOT / "hermes_cli").rglob("*.py"))
    values.extend((SOURCE_ROOT / "apps/sigil/src/sigil").rglob("*.py"))
    return tuple(sorted(values))


def _verify_tailnet(node_id: str, dns_name: str) -> None:
    completed = subprocess.run(
        ["tailscale", "status", "--json"],
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise ValueError("tailnet identity inventory is unavailable")
    value = json.loads(completed.stdout)
    nodes = [value.get("Self", {})] + list(value.get("Peer", {}).values())
    matches = [
        item
        for item in nodes
        if item.get("ID") == node_id
        and item.get("DNSName", "").rstrip(".") == dns_name.rstrip(".")
        and item.get("Online") is True
    ]
    if len(matches) != 1:
        raise ValueError("target tailnet identity is unavailable or ambiguous")


def _validated_bundle(args: argparse.Namespace) -> tuple[bytes, SignedServiceConfig]:
    config = SignedServiceConfig.load(args.service_config)
    if config.local_node_id != args.node_id:
        raise ValueError(
            "service configuration node does not match the explicit target"
        )
    registry = CredentialRegistry.load(args.credential_registry)
    bound = [
        item
        for item in registry.credentials
        if item.coordinator_node_id == config.coordinator_node_id
        and item.target_node_id == config.local_node_id
    ]
    if not bound:
        raise ValueError("credential registry is not bound to the explicit target")
    secrets: dict[str, bytes] = {}
    rewritten = []
    for item in bound:
        material = resolve_secret(item.secret_reference)
        name = f"{item.credential_id}.secret"
        secrets[name] = material + b"\n"
        rewritten.append(
            item.model_copy(
                update={"secret_reference": f"file:/etc/hermes-link/secrets/{name}"}
            )
        )
    remote_registry = CredentialRegistry(credentials=tuple(rewritten))
    remote_config = config.model_copy(
        update={"credential_registry_path": Path("/etc/hermes-link/credentials.json")}
    )
    source_files = _source_files()
    source_hash = hashlib.sha256()
    for path in source_files:
        source_hash.update(str(path.relative_to(SOURCE_ROOT)).encode() + b"\0")
        source_hash.update(path.read_bytes())
    release_id = source_hash.hexdigest()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        values = {
            "service.json": remote_config.model_dump_json(indent=2).encode() + b"\n",
            "credentials.json": remote_registry.model_dump_json(indent=2).encode()
            + b"\n",
            "hermes-link.service": UNIT.read_bytes(),
            "source-revision": (release_id + "\n").encode(),
            **{f"secrets/{name}": material for name, material in secrets.items()},
        }
        for path in source_files:
            values[f"source/{path.relative_to(SOURCE_ROOT)}"] = path.read_bytes()
        for name, material in values.items():
            info = tarfile.TarInfo(name)
            info.size = len(material)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(material))
    return stream.getvalue(), remote_config


def _remote_command() -> str:
    return " ".join((
        "set -eu;",
        'test "$(hostname)" = "$1";',
        "stage=$(mktemp -d /tmp/hermes-link-deploy.XXXXXX);",
        'trap \'find "$stage" -type f -exec shred -u {} + 2>/dev/null || true; find "$stage" -depth -type d -exec rmdir {} + 2>/dev/null || true\' EXIT;',
        'tar -x -C "$stage";',
        'revision=$(cat "$stage/source-revision"); case "$revision" in *[!0-9a-f]*|"") exit 1;; esac;',
        "if ! id hermes >/dev/null 2>&1; then sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin hermes; fi;",
        "sudo install -d -o root -g root -m 0755 /opt/hermes-link/releases;",
        'if test ! -d "/opt/hermes-link/releases/$revision"; then sudo install -d -o root -g root -m 0755 "/opt/hermes-link/releases/$revision"; sudo cp -R "$stage/source/." "/opt/hermes-link/releases/$revision/"; sudo find "/opt/hermes-link/releases/$revision" -type d -exec chmod 0755 {} +; sudo find "/opt/hermes-link/releases/$revision" -type f -exec chmod 0644 {} +; fi;',
        'sudo ln -sfn "/opt/hermes-link/releases/$revision" /opt/hermes-link/current;',
        "if test ! -x /opt/hermes-link/venv/bin/python; then if test -x /opt/hermes/current/venv/bin/python; then sudo ln -s /opt/hermes/current/venv /opt/hermes-link/venv; else sudo python3 -m venv /opt/hermes-link/venv; sudo /opt/hermes-link/venv/bin/python -m pip install --disable-pip-version-check --no-input 'fastapi==0.133.1' 'uvicorn==0.41.0' 'pydantic==2.13.4' 'httpx==0.28.1' 'websockets==15.0.1'; fi; fi;",
        "PYTHONPATH=/opt/hermes-link/current:/opt/hermes-link/current/apps/sigil/src /opt/hermes-link/venv/bin/python -c 'import hermes_cli.hermes_link.runtime, sigil.ai.fleet';",
        "sudo install -d -o root -g hermes -m 0750 /etc/hermes-link;",
        "sudo install -d -o root -g hermes -m 0750 /etc/hermes-link/secrets;",
        "sudo install -d -o hermes -g hermes -m 0700 /var/lib/hermes-link;",
        'sudo install -o root -g hermes -m 0640 "$stage/service.json" /etc/hermes-link/service.json;',
        'sudo install -o hermes -g hermes -m 0600 "$stage/credentials.json" /etc/hermes-link/credentials.json;',
        'sudo find /etc/hermes-link/secrets -maxdepth 1 -type f -name "*.secret" -delete;',
        'for item in "$stage"/secrets/*.secret; do sudo install -o hermes -g hermes -m 0600 "$item" /etc/hermes-link/secrets/"$(basename "$item")"; done;',
        'sudo install -o root -g root -m 0644 "$stage/hermes-link.service" /etc/systemd/system/hermes-link.service;',
        "sudo systemctl daemon-reload;",
        "sudo systemctl enable hermes-link.service;",
        "sudo systemctl restart hermes-link.service;",
        'ready=false; for attempt in 1 2 3 4 5 6 7 8 9 10; do if sudo systemctl is-active --quiet hermes-link.service; then ready=true; break; fi; sleep 1; done; test "$ready" = true;',
    ))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Deploy signed Hermes Link without exposing secrets"
    )
    value.add_argument("--ssh-target", required=True)
    value.add_argument("--tailnet-dns", required=True)
    value.add_argument("--tailnet-node-id", required=True)
    value.add_argument("--expected-hostname", required=True)
    value.add_argument("--node-id", required=True)
    value.add_argument("--service-config", required=True, type=Path)
    value.add_argument("--credential-registry", required=True, type=Path)
    value.add_argument(
        "--transport", choices=("tailscale-ssh", "ssh"), default="tailscale-ssh"
    )
    value.add_argument("--install", action="store_true")
    return value


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.ssh_target.rsplit("@", 1)[-1].rstrip(
            "."
        ) != arguments.tailnet_dns.rstrip("."):
            raise ValueError("SSH target must use the verified tailnet DNS identity")
        _verify_tailnet(arguments.tailnet_node_id, arguments.tailnet_dns)
        bundle, config = _validated_bundle(arguments)
        if not arguments.install:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "node_id": config.local_node_id,
                        "tailnet_identity": arguments.tailnet_node_id,
                        "tailnet_dns": arguments.tailnet_dns,
                        "authentication": "signed_hmac_sha256",
                        "secret_material": "not_displayed",
                    },
                    sort_keys=True,
                )
            )
            return 0
        transport = (
            ["tailscale", "ssh", arguments.ssh_target]
            if arguments.transport == "tailscale-ssh"
            else [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                arguments.ssh_target,
            ]
        )
        install_arguments = [
            "sh",
            "-c",
            _remote_command(),
            "hermes-link-install",
            arguments.expected_hostname,
        ]
        if arguments.transport == "tailscale-ssh":
            install_arguments = [shlex.join(install_arguments)]
        subprocess.run(
            transport + install_arguments,
            input=bundle,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        verification_arguments = [
            "sh",
            "-c",
            "sudo systemctl is-active --quiet hermes-link.service && sudo -u hermes env PYTHONPATH=/opt/hermes-link/current:/opt/hermes-link/current/apps/sigil/src /opt/hermes-link/venv/bin/python -m hermes_cli.hermes_link.runtime --config /etc/hermes-link/service.json --validate >/dev/null",
        ]
        if arguments.transport == "tailscale-ssh":
            verification_arguments = [shlex.join(verification_arguments)]
        verification = subprocess.run(
            transport + verification_arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if verification.returncode:
            raise ValueError("remote deployment failed closed")
        print(
            json.dumps(
                {
                    "ok": True,
                    "installed": True,
                    "node_id": config.local_node_id,
                    "service": "active",
                    "secret_material": "not_displayed",
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
