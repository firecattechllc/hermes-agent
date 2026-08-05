#!/usr/bin/env python3
"""Non-destructive smoke test for the FireCat Hermes capability activation.

Every check in this script is read-only: it loads/validates the capability
manifest, runs read-only `hermes` subcommands (`doctor`, `cron status`,
`mission-control status`, `link status`), checks for the *presence* (never
the value) of expected credentials, and optionally verifies Tailscale
connectivity to named fleet nodes. It never installs a service, never
writes application state, never prints a secret value, and never exits
non-zero for a credential that is expected to be absent in this environment
(those are reported as SKIP, not FAIL).

Usage:
    python scripts/smoke_test_fleet.py
    python scripts/smoke_test_fleet.py --titan-dns-identity hydra-titan.example.ts.net
    python scripts/smoke_test_fleet.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SUBPROCESS_TIMEOUT_SECONDS = 20

# Credentials this deployment cares about, mapped to the manifest key that
# explains what they unlock. Only the NAME is ever inspected/printed.
CREDENTIAL_CHECKS = {
    "TELEGRAM_BOT_TOKEN": "telegram_gateway",
    "HERMES_LINK_TOKEN": "hermes_link_titan_node / hermes_link_mac_node",
    "GITHUB_TOKEN": "source-of-truth requirement",
}


class CheckOutcome(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    outcome: CheckOutcome
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "outcome": self.outcome.value, "detail": self.detail}


def _run_hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, read-only subcommands only
        ["hermes", *args],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
        cwd=str(REPO_ROOT),
    )


def check_capability_manifest() -> CheckResult:
    try:
        from hermes_cli.capability_manifest import (
            load_capability_manifest,
            validate_capability_manifest,
        )

        manifest = load_capability_manifest()
        ok, warnings = validate_capability_manifest(manifest)
        counts = manifest.counts_by_state()
        detail = f"{len(manifest.entries)} entries, counts={counts}"
        if not ok:
            return CheckResult("capability_manifest", CheckOutcome.WARN, f"{detail}; lint warnings: {warnings}")
        return CheckResult("capability_manifest", CheckOutcome.PASS, detail)
    except Exception as error:  # noqa: BLE001
        return CheckResult("capability_manifest", CheckOutcome.FAIL, str(error))


def check_hermes_cli_available() -> CheckResult:
    try:
        proc = subprocess.run(  # noqa: S603, S607
            ["hermes", "--version"], capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError:
        return CheckResult("hermes_cli_available", CheckOutcome.FAIL, "`hermes` not found on PATH")
    except subprocess.TimeoutExpired:
        return CheckResult("hermes_cli_available", CheckOutcome.FAIL, "`hermes --version` timed out")
    if proc.returncode != 0:
        return CheckResult("hermes_cli_available", CheckOutcome.FAIL, f"exit={proc.returncode}")
    return CheckResult("hermes_cli_available", CheckOutcome.PASS, proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "ok")


def check_hermes_doctor() -> CheckResult:
    try:
        proc = _run_hermes("doctor")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return CheckResult("hermes_doctor", CheckOutcome.FAIL, str(error))
    outcome = CheckOutcome.PASS if proc.returncode == 0 else CheckOutcome.WARN
    return CheckResult("hermes_doctor", outcome, f"exit={proc.returncode}")


def check_cron_status() -> CheckResult:
    try:
        proc = _run_hermes("cron", "status")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return CheckResult("cron_status", CheckOutcome.FAIL, str(error))
    combined = (proc.stdout or proc.stderr).strip()
    detail = combined.splitlines()[0] if combined else f"exit={proc.returncode}"
    if proc.returncode != 0:
        return CheckResult("cron_status", CheckOutcome.FAIL, detail)
    if "not running" in combined.lower():
        return CheckResult("cron_status", CheckOutcome.WARN, detail)
    return CheckResult("cron_status", CheckOutcome.PASS, detail)


def check_mission_control_status() -> CheckResult:
    try:
        proc = _run_hermes("mission-control", "status")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return CheckResult("mission_control_status", CheckOutcome.FAIL, str(error))
    outcome = CheckOutcome.PASS if proc.returncode == 0 else CheckOutcome.WARN
    return CheckResult("mission_control_status", outcome, f"exit={proc.returncode}")


def check_hermes_link_status() -> CheckResult:
    try:
        proc = _run_hermes("link", "status")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return CheckResult("hermes_link_status", CheckOutcome.FAIL, str(error))
    if proc.returncode == 0:
        return CheckResult("hermes_link_status", CheckOutcome.PASS, "reachable")
    combined = (proc.stdout + proc.stderr).strip()
    if "HERMES_LINK_TOKEN" in combined or "not configured" in combined:
        return CheckResult(
            "hermes_link_status", CheckOutcome.SKIP,
            "HERMES_LINK_TOKEN not configured in this environment (expected until deployment)",
        )
    return CheckResult("hermes_link_status", CheckOutcome.WARN, combined.splitlines()[0] if combined else f"exit={proc.returncode}")


def check_credential_presence() -> List[CheckResult]:
    results = []
    for var_name, unlocks in CREDENTIAL_CHECKS.items():
        present = bool(os.environ.get(var_name)) or _present_in_hermes_env(var_name)
        outcome = CheckOutcome.PASS if present else CheckOutcome.SKIP
        detail = f"unlocks: {unlocks}" if present else f"not set (unlocks: {unlocks})"
        results.append(CheckResult(f"credential_present:{var_name}", outcome, detail))
    return results


def _present_in_hermes_env(var_name: str) -> bool:
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return False
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{var_name}=") and len(stripped) > len(var_name) + 1:
                return True
    except OSError:
        return False
    return False


def check_deploy_templates_present() -> CheckResult:
    expected = [
        "deploy/titan/titan.env.example",
        "deploy/mac/mac.env.example",
        "deploy/mac/mac-coordinator.json.example",
        "deploy/secrets.env.example",
        "deploy/hermes-link/titan.service.json.example",
        "deploy/hermes-link/hermes-link.service",
    ]
    missing = [p for p in expected if not (REPO_ROOT / p).exists()]
    if missing:
        return CheckResult("deploy_templates_present", CheckOutcome.FAIL, f"missing: {missing}")
    return CheckResult("deploy_templates_present", CheckOutcome.PASS, f"{len(expected)} templates present")


def check_tailscale_node(dns_identity: Optional[str], label: str) -> CheckResult:
    if dns_identity is None:
        return CheckResult(f"tailscale_{label}", CheckOutcome.SKIP, "no --{}-dns-identity given".format(label))
    from scripts.fleet_connectivity_check import check_node_connectivity

    result = check_node_connectivity(dns_identity)
    outcome = CheckOutcome.PASS if result.verified else CheckOutcome.WARN
    return CheckResult(f"tailscale_{label}", outcome, f"{result.reason} (online={result.peer_online})")


def check_computer_use_doctor() -> CheckResult:
    if sys.platform != "darwin":
        return CheckResult("computer_use_doctor", CheckOutcome.SKIP, "not macOS")
    try:
        proc = _run_hermes("computer_use", "doctor")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return CheckResult("computer_use_doctor", CheckOutcome.FAIL, str(error))
    outcome = CheckOutcome.PASS if proc.returncode == 0 else CheckOutcome.WARN
    return CheckResult("computer_use_doctor", outcome, f"exit={proc.returncode}")


def run_all_checks(*, titan_dns_identity: Optional[str], mac_dns_identity: Optional[str]) -> List[CheckResult]:
    checks: List[Callable[[], object]] = [
        check_hermes_cli_available,
        check_capability_manifest,
        check_deploy_templates_present,
        check_hermes_doctor,
        check_cron_status,
        check_mission_control_status,
        check_hermes_link_status,
        check_computer_use_doctor,
    ]
    results: List[CheckResult] = []
    for check in checks:
        results.append(check())
    results.extend(check_credential_presence())
    results.append(check_tailscale_node(titan_dns_identity, "titan"))
    results.append(check_tailscale_node(mac_dns_identity, "mac"))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--titan-dns-identity", default=None, help="Titan's Tailscale MagicDNS name, to also verify connectivity")
    parser.add_argument("--mac-dns-identity", default=None, help="Mac's Tailscale MagicDNS name, to also verify connectivity")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args(argv)

    results = run_all_checks(titan_dns_identity=args.titan_dns_identity, mac_dns_identity=args.mac_dns_identity)

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        print("FireCat Hermes fleet smoke test (non-destructive, read-only)\n")
        for r in results:
            marker = {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}[r.outcome.value]
            print(f"  {marker} {r.name:<32} {r.outcome.value:<6} {r.detail}")

    failed = [r for r in results if r.outcome == CheckOutcome.FAIL]
    if not args.json:
        print()
        print(f"{len(results)} checks: "
              f"{sum(r.outcome == CheckOutcome.PASS for r in results)} passed, "
              f"{sum(r.outcome == CheckOutcome.WARN for r in results)} warned, "
              f"{len(failed)} failed, "
              f"{sum(r.outcome == CheckOutcome.SKIP for r in results)} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
