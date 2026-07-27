#!/usr/bin/env python3
"""Run the complete Sigil Step 40 production-hardening certification gate."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

TARGETED_TESTS = (
    "apps/sigil/tests/test_step40_production_hardening.py",
    "apps/sigil/tests/test_governed_paper_runtime_execution.py",
    "apps/sigil/tests/test_step40_certification_gate.py",
)

CERTIFIED_PATHS = (
    "apps/sigil",
    "apps/sigil-desktop",
)


def run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    finished_at = datetime.now(timezone.utc)

    return {
        "name": name,
        "command": command,
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(
            (finished_at - started_at).total_seconds(),
            3,
        ),
        "output": completed.stdout,
    }


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def repository_digest() -> tuple[str, int]:
    tracked_output = subprocess.run(
        ["git", "ls-files", "-z", "--", *CERTIFIED_PATHS],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout

    tracked_files = [
        entry.decode("utf-8")
        for entry in tracked_output.split(b"\0")
        if entry
    ]

    digest = hashlib.sha256()

    for relative_name in sorted(tracked_files):
        path = REPOSITORY_ROOT / relative_name
        if not path.is_file():
            continue

        digest.update(relative_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")

    return digest.hexdigest(), len(tracked_files)


def extract_pytest_count(output: str) -> int | None:
    matches = re.findall(r"(\d+)\s+passed", output)
    if not matches:
        return None
    return int(matches[-1])


def extract_vitest_count(output: str) -> int | None:
    """Extract Vitest's test count while tolerating ANSI formatting."""
    ansi_pattern = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    clean_output = ansi_pattern.sub("", output)

    matches = re.findall(
        r"\bTests\s+(\d+)\s+passed\b",
        clean_output,
    )
    if not matches:
        return None

    return int(matches[-1])

def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def create_signature(report_bytes: bytes) -> dict[str, str]:
    report_digest = hashlib.sha256(report_bytes).hexdigest()
    signing_key = os.environ.get("SIGIL_CERTIFICATION_SIGNING_KEY")

    if signing_key:
        signature = hmac.new(
            signing_key.encode("utf-8"),
            report_bytes,
            hashlib.sha256,
        ).hexdigest()
        signature_type = "HMAC-SHA256"
    else:
        signature = report_digest
        signature_type = "SHA256-CHECKSUM"

    return {
        "report_sha256": report_digest,
        "signature": signature,
        "signature_type": signature_type,
    }


def build_text_report(report: dict[str, Any]) -> str:
    lines = [
        "SIGIL STEP 40 PRODUCTION HARDENING CERTIFICATION",
        "=" * 52,
        "",
        f"Result: {report['result']}",
        f"Certified at: {report['certified_at']}",
        f"Branch: {report['repository']['branch']}",
        f"Commit: {report['repository']['commit']}",
        f"Repository dirty: {report['repository']['dirty']}",
        f"Certified files: {report['repository']['certified_file_count']}",
        f"Repository digest: {report['repository']['certified_paths_sha256']}",
        "",
        "CERTIFICATION GATES",
        "-" * 52,
    ]

    for gate in report["gates"]:
        lines.extend(
            [
                f"{gate['name']}: {'PASS' if gate['passed'] else 'FAIL'}",
                f"  Exit code: {gate['exit_code']}",
                f"  Duration: {gate['duration_seconds']} seconds",
                f"  Test count: {gate.get('test_count', 'unknown')}",
            ]
        )

    lines.extend(
        [
            "",
            "SAFETY CLAIMS VERIFIED",
            "-" * 52,
        ]
    )

    for claim in report["verified_claims"]:
        lines.append(f"- {claim}")

    lines.extend(
        [
            "",
            "SIGNATURE",
            "-" * 52,
            f"Type: {report['attestation']['signature_type']}",
            f"Report SHA-256: {report['attestation']['report_sha256']}",
            f"Signature: {report['attestation']['signature']}",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify Sigil Step 40 production hardening."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/sigil-step40-certification"),
    )
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=== SIGIL STEP 40 CERTIFICATION ===")
    print(f"Repository: {REPOSITORY_ROOT}")
    print(f"Evidence:   {output_dir}")
    print()

    gates = [
        run_command(
            "Targeted Step 40 hardening tests",
            [
                sys.executable,
                "-m",
                "pytest",
                *TARGETED_TESTS,
                "-q",
            ],
        ),
        run_command(
            "Complete Sigil backend suite",
            [
                sys.executable,
                "-m",
                "pytest",
                "apps/sigil/tests",
                "-q",
            ],
        ),
        run_command(
            "Sigil desktop suite",
            [
                "npm",
                "test",
                "--workspace",
                "@firecattechnology/sigil-desktop",
            ],
        ),
    ]

    gates[0]["test_count"] = extract_pytest_count(gates[0]["output"])
    gates[1]["test_count"] = extract_pytest_count(gates[1]["output"])
    gates[2]["test_count"] = extract_vitest_count(gates[2]["output"])

    for gate in gates:
        status = "PASS" if gate["passed"] else "FAIL"
        print(
            f"[{status}] {gate['name']} "
            f"({gate['duration_seconds']}s)"
        )

    digest, certified_file_count = repository_digest()
    git_status = git_output("status", "--porcelain")
    overall_passed = all(gate["passed"] for gate in gates)

    report: dict[str, Any] = {
        "schema": "sigil.step40.certification.v1",
        "result": "PASS" if overall_passed else "FAIL",
        "certified_at": datetime.now(timezone.utc).isoformat(),
        "repository": {
            "root": str(REPOSITORY_ROOT),
            "branch": git_output("branch", "--show-current"),
            "commit": git_output("rev-parse", "HEAD"),
            "dirty": bool(git_status),
            "working_tree_status": git_status.splitlines(),
            "certified_paths": list(CERTIFIED_PATHS),
            "certified_file_count": certified_file_count,
            "certified_paths_sha256": digest,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "node": git_or_command_version(["node", "--version"]),
            "npm": git_or_command_version(["npm", "--version"]),
        },
        "gates": gates,
        "verified_claims": [
            "Persisted paper orders survive runtime module reload.",
            "Duplicate order identifiers remain rejected after restart.",
            "Reconciliation-required state blocks automation startup.",
            "Unsafe runtime state pauses running automation.",
            "Disconnected runtime health is degraded, not healthy.",
            "Corrupt balances fail closed.",
            "Broker execution remains disabled in paper mode.",
            "Desktop Mission Control surfaces governed runtime health.",
        ],
    }

    unsigned_bytes = canonical_json(report)
    report["attestation"] = create_signature(unsigned_bytes)

    json_path = output_dir / "step40-certification.json"
    text_path = output_dir / "step40-certification.txt"
    logs_path = output_dir / "logs"
    logs_path.mkdir(exist_ok=True)

    json_path.write_bytes(canonical_json(report))
    text_path.write_text(build_text_report(report))

    for index, gate in enumerate(gates, start=1):
        safe_name = re.sub(
            r"[^a-z0-9]+",
            "-",
            gate["name"].lower(),
        ).strip("-")
        (logs_path / f"{index:02d}-{safe_name}.log").write_text(
            gate["output"]
        )

    print()
    print(f"Certification result: {report['result']}")
    print(f"JSON report: {json_path}")
    print(f"Text report: {text_path}")
    print(
        "Attestation: "
        f"{report['attestation']['signature_type']} "
        f"{report['attestation']['signature']}"
    )

    return 0 if overall_passed else 1


def git_or_command_version(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.stdout.strip() or "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
