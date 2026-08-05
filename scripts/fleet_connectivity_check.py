#!/usr/bin/env python3
"""Fail-closed Tailscale connectivity + node-identity verification.

Non-destructive and read-only: this script only runs `tailscale status
--json` (a local, non-mutating query against the already-running
`tailscaled` daemon) and compares its output against an expected node
identity. It never sends Hermes Link traffic, never mutates Tailscale
state, and never requires elevated privileges.

Fail-closed by design: a node is only ever reported "verified" if every one
of these positively holds — `tailscale` is installed, `tailscaled` is
running, the named peer is present in the tailnet peer list, it is
currently reported online, and (if an expected hostname was supplied) that
hostname matches. Any missing, ambiguous, or unreachable state is reported
as NOT verified, never defaulted to "probably fine."

Usage:
    python scripts/fleet_connectivity_check.py --dns-identity hydra-titan.example.ts.net
    python scripts/fleet_connectivity_check.py --node titan --config deploy/mac/mac-coordinator.json
    python scripts/fleet_connectivity_check.py --node titan --config deploy/mac/mac-coordinator.json --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

TAILSCALE_STATUS_TIMEOUT_SECONDS = 10


class FleetConnectivityCheckError(RuntimeError):
    """The connectivity check could not be completed at all (as opposed to
    completing and reporting the node unreachable)."""


@dataclass(frozen=True)
class NodeConnectivityResult:
    node: str
    tailscale_running: bool
    peer_found: bool
    peer_online: Optional[bool]
    peer_hostname: Optional[str]
    verified: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _run_tailscale_status() -> Optional[dict]:
    """Run `tailscale status --json`. Returns None (never raises) if the
    tailscale binary is missing, times out, or returns anything that isn't
    parseable JSON — all treated identically as "cannot verify" upstream."""
    try:
        proc = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell, read-only
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=TAILSCALE_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_dns_identity(value: str) -> str:
    return value.strip().rstrip(".").lower()


def check_node_connectivity(
    dns_identity: str,
    *,
    expected_hostname: Optional[str] = None,
    status: Optional[dict] = None,
) -> NodeConnectivityResult:
    """Fail-closed connectivity check for one Tailscale-identified node.

    ``status`` may be injected for deterministic testing; production
    callers should omit it (a real `tailscale status --json` call is made).
    """
    node = _normalize_dns_identity(dns_identity)
    if status is None:
        status = _run_tailscale_status()

    if status is None:
        return NodeConnectivityResult(
            node=node,
            tailscale_running=False,
            peer_found=False,
            peer_online=None,
            peer_hostname=None,
            verified=False,
            reason="tailscale_unavailable_or_not_running",
        )

    if status.get("BackendState") != "Running":
        return NodeConnectivityResult(
            node=node,
            tailscale_running=False,
            peer_found=False,
            peer_online=None,
            peer_hostname=None,
            verified=False,
            reason="tailscale_backend_not_running",
        )

    candidates = list((status.get("Peer") or {}).values())
    self_peer = status.get("Self")
    if isinstance(self_peer, dict):
        candidates.append(self_peer)

    match = None
    for peer in candidates:
        if not isinstance(peer, dict):
            continue
        peer_dns = _normalize_dns_identity(str(peer.get("DNSName") or ""))
        peer_hostname = str(peer.get("HostName") or "")
        if peer_dns == node or peer_hostname.lower() == node:
            match = peer
            break

    if match is None:
        return NodeConnectivityResult(
            node=node,
            tailscale_running=True,
            peer_found=False,
            peer_online=None,
            peer_hostname=None,
            verified=False,
            reason="node_not_found_in_tailnet",
        )

    hostname = match.get("HostName")
    online = bool(match.get("Online", False))

    if expected_hostname is not None and hostname != expected_hostname:
        return NodeConnectivityResult(
            node=node,
            tailscale_running=True,
            peer_found=True,
            peer_online=online,
            peer_hostname=hostname,
            verified=False,
            reason="hostname_mismatch",
        )

    if not online:
        return NodeConnectivityResult(
            node=node,
            tailscale_running=True,
            peer_found=True,
            peer_online=False,
            peer_hostname=hostname,
            verified=False,
            reason="node_offline",
        )

    return NodeConnectivityResult(
        node=node,
        tailscale_running=True,
        peer_found=True,
        peer_online=True,
        peer_hostname=hostname,
        verified=True,
        reason="ok",
    )


def _load_dns_identity_from_config(config_path: Path, node_key: str) -> str:
    """Read a target's tailnet_dns_identity out of a mac-coordinator.json
    -shaped config file. Fails closed (raises) rather than guessing on any
    malformed or ambiguous config."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FleetConnectivityCheckError(
            f"cannot read config {config_path}: {error}"
        ) from error

    targets = raw.get("targets")
    if not isinstance(targets, list):
        raise FleetConnectivityCheckError(f"{config_path} has no 'targets' list")

    matches = [
        t
        for t in targets
        if isinstance(t, dict) and node_key in str(t.get("node_id", ""))
    ]
    if not matches:
        raise FleetConnectivityCheckError(
            f"no target matching {node_key!r} found in {config_path}"
        )
    if len(matches) > 1:
        raise FleetConnectivityCheckError(
            f"ambiguous: {len(matches)} targets matching {node_key!r} in {config_path}"
        )
    identity = matches[0].get("tailnet_dns_identity")
    if not identity or not isinstance(identity, str):
        raise FleetConnectivityCheckError(
            f"target {node_key!r} in {config_path} has no tailnet_dns_identity"
        )
    if identity.startswith("replace-with-verified"):
        raise FleetConnectivityCheckError(
            f"target {node_key!r} in {config_path} still has a placeholder "
            "tailnet_dns_identity — fill in the real value before checking connectivity"
        )
    return identity


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--dns-identity", help="Tailscale MagicDNS name to check directly"
    )
    source.add_argument(
        "--node",
        help="Node key to look up in --config (matched against target node_id)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="mac-coordinator.json-shaped config file (required with --node)",
    )
    parser.add_argument(
        "--expected-hostname", default=None, help="Optional exact HostName to require"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of text"
    )
    args = parser.parse_args(argv)

    if args.node and not args.config:
        parser.error("--node requires --config")

    try:
        if args.dns_identity:
            dns_identity = args.dns_identity
        else:
            dns_identity = _load_dns_identity_from_config(args.config, args.node)
    except FleetConnectivityCheckError as error:
        if args.json:
            print(
                json.dumps({
                    "verified": False,
                    "reason": "config_error",
                    "detail": str(error),
                })
            )
        else:
            print(f"fleet-connectivity-check: {error}", file=sys.stderr)
        return 2

    result = check_node_connectivity(
        dns_identity, expected_hostname=args.expected_hostname
    )

    if args.json:
        print(json.dumps(result.to_dict(), sort_keys=True))
    else:
        status_word = "VERIFIED" if result.verified else "NOT VERIFIED"
        print(f"{result.node}: {status_word} ({result.reason})")
        print(f"  tailscale_running: {result.tailscale_running}")
        print(f"  peer_found:        {result.peer_found}")
        print(f"  peer_online:       {result.peer_online}")
        print(f"  peer_hostname:     {result.peer_hostname}")

    return 0 if result.verified else 1


if __name__ == "__main__":
    sys.exit(main())
