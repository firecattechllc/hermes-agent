#!/usr/bin/env python3
"""Deterministic local Step 33 focused certification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = (
    ("compile", (sys.executable, "-m", "compileall", "-q", "hermes_cli/knowledge")),
    (
        "focused_tests",
        (sys.executable, "-m", "pytest", "tests/hermes_cli/test_knowledge", "-q"),
    ),
    (
        "lint",
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "hermes_cli/knowledge",
            "hermes_cli/subcommands/knowledge.py",
            "tests/hermes_cli/test_knowledge",
        ),
    ),
    ("cli_help", (sys.executable, "-m", "hermes_cli.main", "knowledge", "--help")),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="hermes-step33-") as temporary:
        env = {
            "PATH": str(Path(sys.executable).parent),
            "PYTHONPATH": str(ROOT),
            "HERMES_KNOWLEDGE_DB": str(Path(temporary) / "graph.sqlite3"),
        }
        for name, command in CHECKS:
            completed = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, check=False
            )
            results.append({
                "check": name,
                "passed": completed.returncode == 0,
                "returncode": completed.returncode,
                "output": (completed.stdout + completed.stderr)[-4000:],
            })
    payload = {
        "step": 33,
        "passed": all(item["passed"] for item in results),
        "checks": results,
    }
    print(json.dumps(payload, indent=2 if args.json else None))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
