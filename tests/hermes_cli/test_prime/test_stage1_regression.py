"""Confirms the immutable Stage 1 safety/certification baseline still holds.

This test invokes the real, unmodified Stage 1 verification scripts
(``apps/sigil/scripts/verify_certification_evidence.py`` and
``apps/sigil/scripts/verify_public_execution_isolation.py``) exactly as
they exist on disk. It never edits, mocks, or bypasses either script. A
failure here means the Stage 1 baseline actually regressed, not that this
test is broken.
"""

from __future__ import annotations

from pathlib import Path

from hermes_cli.prime.certification import run_stage1_regression

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_stage1_public_execution_isolation_guard_still_passes() -> None:
    passed, detail = run_stage1_regression(repo_root=_REPO_ROOT, timeout_seconds=120)
    assert passed, detail
