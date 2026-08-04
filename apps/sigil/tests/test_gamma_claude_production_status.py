from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sigil.ai import GammaClaudeProductionStatus, gamma_claude_production_status

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "sigil"


@pytest.mark.parametrize(
    ("wired", "enabled", "expected"),
    [
        (False, False, GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED),
        (False, True, GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED),
        (True, False, GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_DISABLED),
        (True, True, GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED),
    ],
)
def test_status_is_derived_from_both_inputs(
    wired: bool,
    enabled: bool,
    expected: GammaClaudeProductionStatus,
) -> None:
    assert (
        gamma_claude_production_status(
            wired_into_production_runtime=wired,
            config_enabled=enabled,
        )
        == expected
    )


def test_wiring_alone_without_enablement_is_never_fully_enabled() -> None:
    status = gamma_claude_production_status(
        wired_into_production_runtime=True,
        config_enabled=False,
    )

    assert status != GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED


def test_enablement_alone_without_wiring_is_never_fully_enabled() -> None:
    status = gamma_claude_production_status(
        wired_into_production_runtime=False,
        config_enabled=True,
    )

    assert status != GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED
    assert status == GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED


def _references_hermes_claude_provider(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "HermesClaudeProvider":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "HermesClaudeProvider":
            return True
    return False


def test_claude_provider_is_not_wired_into_any_production_entry_point() -> None:
    """Canary: HermesClaudeProvider must stay unreferenced outside sigil.ai.

    This regression test enforces the GAMMA-005 finding at the source level:
    the governed Claude provider is implemented and unit-tested, but as of
    this revision no production entry point (desktop bridge runtime,
    registry wiring, routing tables) instantiates or references it. If this
    test starts failing, that is a real change in production status and the
    Gamma readiness/signoff callers must be updated to pass
    ``claude_wired_into_production_runtime=True`` truthfully — this test
    should never be "fixed" by weakening the assertion.
    """

    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        if path.parent.name == "ai" and path.parent.parent.name == "sigil":
            continue
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        if _references_hermes_claude_provider(path):
            offenders.append(str(path.relative_to(SRC_ROOT)))

    assert offenders == [], (
        "HermesClaudeProvider is now referenced outside sigil.ai: "
        f"{offenders}. Update Gamma readiness callers to pass "
        "claude_wired_into_production_runtime=True with real evidence."
    )
