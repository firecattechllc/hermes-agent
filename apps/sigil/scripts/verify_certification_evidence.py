#!/usr/bin/env python3
"""Fail-closed CI gate for Sigil certification evidence integrity.

Verifies:

1. Every markdown artifact under ``certification/claude-review/`` parses to
   a recognized, self-consistent evidence status (never a silent parse
   failure being treated as "fine").
2. The Golden Master certification doc declares an explicit fleet
   failover/high-availability evidence status rather than leaving it to be
   inferred from an unrelated Home Assistant test suite.
3. The fleet failover placeholder document itself parses as truthful,
   non-certifying evidence.

This script does not require every artifact to be certifying -- a
truthfully failed review (status ``execution_error``, ``certifying: false``)
passes this gate. What it refuses to pass is an artifact that is malformed,
missing required fields, or self-contradictory (e.g. declaring
``certifying: true`` under a non-certifying status).

Run directly, from within ``apps/sigil`` (where ``sigil`` is installed
editable, so it resolves with no extra setup):

    cd apps/sigil && uv run python scripts/verify_certification_evidence.py

Or from the repository root, with ``sigil`` made importable explicitly via
``PYTHONPATH`` rather than relying on ``apps/sigil``'s own environment:

    PYTHONPATH=apps/sigil/src uv run python apps/sigil/scripts/verify_certification_evidence.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sigil.certification.evidence import (
    CertificationEvidenceError,
    CertificationEvidenceStatus,
    load_evidence_artifact,
    parse_metadata,
)

# This module's own `import sigil...` above resolves one of two ways:
#   - `sigil` is installed editable as part of the apps/sigil project (see
#     apps/sigil/pyproject.toml), so it just works when run from within
#     apps/sigil (e.g. `uv run` there, or CI's working-directory: apps/sigil).
#   - otherwise, the caller must put apps/sigil/src on PYTHONPATH explicitly
#     (see the module docstring) -- deliberately not done here via sys.path
#     mutation, so the import contract stays visible at the call site
#     instead of being silently patched at runtime.
SIGIL_ROOT = Path(__file__).resolve().parents[1]  # apps/sigil
REPOSITORY_ROOT = SIGIL_ROOT.parents[1]

CLAUDE_REVIEW_DIR = REPOSITORY_ROOT / "certification" / "claude-review"
GOLDEN_MASTER_DOC = (
    REPOSITORY_ROOT / "docs" / "certification" / "sigil-golden-master-v3.5.0-post-gamma.md"
)
FLEET_FAILOVER_DOC = (
    REPOSITORY_ROOT / "docs" / "certification" / "sigil-fleet-failover-certification.md"
)

# Fleet failover status values this stage is willing to accept as truthful.
# `missing_evidence` is the only value backed by anything real right now --
# a future stage that actually builds failover certification must update
# this alongside the evidence it adds.
_ACCEPTED_FLEET_FAILOVER_STATUSES = frozenset({"missing_evidence"})


def check_claude_review_artifacts() -> list[str]:
    errors: list[str] = []
    if not CLAUDE_REVIEW_DIR.is_dir():
        return [f"{CLAUDE_REVIEW_DIR}: expected directory does not exist"]
    for path in sorted(CLAUDE_REVIEW_DIR.glob("*.md")):
        artifact = load_evidence_artifact(path)
        if artifact.status in (
            CertificationEvidenceStatus.MALFORMED,
            CertificationEvidenceStatus.MISSING,
        ):
            errors.append(
                f"{path}: evidence artifact is {artifact.status.value} "
                "(missing a recognized Status field, or self-contradictory)"
            )
        else:
            print(f"{path}: status={artifact.status.value} certifying={artifact.certifying}")
    return errors


def check_golden_master_fleet_failover_status() -> list[str]:
    if not GOLDEN_MASTER_DOC.is_file():
        return [f"{GOLDEN_MASTER_DOC}: expected certification document does not exist"]
    metadata = parse_metadata(GOLDEN_MASTER_DOC.read_text(encoding="utf-8"))
    status = metadata.get("fleet_failover_status")
    if status is None:
        return [f"{GOLDEN_MASTER_DOC}: missing required 'Fleet failover status' field"]
    if status not in _ACCEPTED_FLEET_FAILOVER_STATUSES:
        return [
            (
                f"{GOLDEN_MASTER_DOC}: fleet failover status {status!r} is not a "
                "recognized, truthfully-backed value for this stage "
                f"(accepted: {sorted(_ACCEPTED_FLEET_FAILOVER_STATUSES)})"
            )
        ]
    print(f"{GOLDEN_MASTER_DOC}: fleet_failover_status={status}")
    return []


def check_fleet_failover_placeholder() -> list[str]:
    if not FLEET_FAILOVER_DOC.is_file():
        return [f"{FLEET_FAILOVER_DOC}: expected placeholder document does not exist"]
    artifact = load_evidence_artifact(FLEET_FAILOVER_DOC)
    if artifact.status in (
        CertificationEvidenceStatus.MALFORMED,
        CertificationEvidenceStatus.MISSING,
    ):
        return [f"{FLEET_FAILOVER_DOC}: evidence artifact is {artifact.status.value}"]
    if artifact.certifying:
        return [
            (
                f"{FLEET_FAILOVER_DOC}: placeholder declares certifying evidence but no "
                "fleet failover test suite exists in this repository yet"
            )
        ]
    print(f"{FLEET_FAILOVER_DOC}: status={artifact.status.value} certifying={artifact.certifying}")
    return []


def main(argv: list[str] | None = None) -> int:
    del argv
    errors: list[str] = []
    try:
        errors.extend(check_claude_review_artifacts())
        errors.extend(check_golden_master_fleet_failover_status())
        errors.extend(check_fleet_failover_placeholder())
    except CertificationEvidenceError as exc:
        errors.append(str(exc))

    if not errors:
        print("certification evidence guard: OK")
        return 0

    print("certification evidence guard: FAILED", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
