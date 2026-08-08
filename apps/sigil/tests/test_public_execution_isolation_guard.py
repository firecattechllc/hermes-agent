"""Tests for the fail-closed public_execution reachability guard.

See scripts/verify_public_execution_isolation.py. These tests exercise the
guard both against the real repository tree (it must currently pass) and
against synthetic sources (it must reject every reachability path called out
in the Stage 1 safety requirements: direct import, from-import, package
re-export, dynamic import, and registry/factory-style exposure), while
continuing to allow test-only importers and the provider's own
implementation files.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_public_execution_isolation.py"
SIGIL_SRC = Path(__file__).resolve().parents[1] / "src"


def _load_guard():
    spec = importlib.util.spec_from_file_location("verify_public_execution_isolation", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


PRODUCTION_PATH = SIGIL_SRC / "sigil" / "desktop_bridge" / "synthetic_module_for_tests.py"
TEST_PATH = Path(__file__).resolve().parent / "synthetic_test_for_guard.py"


class TestCurrentRepository:
    def test_real_repository_tree_passes(self) -> None:
        violations = guard.scan_tree()
        assert violations == [], "\n".join(str(v) for v in violations)

    def test_provider_implementation_itself_is_excluded(self) -> None:
        provider_file = (
            SIGIL_SRC / "sigil" / "integrations" / "providers" / "public_execution.py"
        )
        assert provider_file.resolve() in guard.EXCLUDED_FILES

    def test_providers_package_init_is_excluded(self) -> None:
        init_file = SIGIL_SRC / "sigil" / "integrations" / "providers" / "__init__.py"
        assert init_file.resolve() in guard.EXCLUDED_FILES

    def test_guard_script_excludes_itself(self) -> None:
        assert SCRIPT_PATH.resolve() in guard.EXCLUDED_FILES


class TestSyntheticProductionImporterIsRejected:
    def test_direct_module_import_rejected(self) -> None:
        source = "import sigil.integrations.providers.public_execution\n"
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "direct-import"

    def test_from_import_of_execution_class_rejected(self) -> None:
        source = (
            "from sigil.integrations.providers.public_execution import (\n"
            "    PublicEquityExecutionProvider,\n"
            ")\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "from-import"
        assert "PublicEquityExecutionProvider" in violations[0].detail

    def test_relative_from_import_of_execution_class_rejected(self) -> None:
        # A file inside sigil/desktop_bridge/ reaching two packages over via a
        # relative import: ..integrations.providers.public_execution
        source = (
            "from ..integrations.providers.public_execution import PublicEquityExecutionProvider\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "from-import"

    def test_package_reexport_import_rejected(self) -> None:
        # Reaching the execution class via the providers package re-export
        # rather than the submodule directly.
        source = "from sigil.integrations.providers import PublicEquityExecutionProvider\n"
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "from-import"

    def test_submodule_import_via_package_rejected(self) -> None:
        source = "from sigil.integrations.providers import public_execution\n"
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "direct-import"

    def test_wildcard_import_rejected(self) -> None:
        source = "from sigil.integrations.providers import *\n"
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "wildcard-import"

    def test_module_alias_attribute_access_rejected(self) -> None:
        source = (
            "import sigil.integrations.providers as providers\n"
            "\n"
            "def build():\n"
            "    return providers.PublicEquityExecutionProvider(None, None, None, None)\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "attribute-access"

    def test_equivalent_execution_factory_name_rejected(self) -> None:
        # A hypothetical future rename/wrapper -- caught by shape, not by an
        # exact-name allowlist.
        source = (
            "from sigil.integrations.providers.public_execution import (\n"
            "    PublicOrderExecutionFactory,\n"
            ")\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "from-import"


class TestDynamicAndRegistryExposureIsRejected:
    def test_importlib_import_module_rejected(self) -> None:
        source = (
            "import importlib\n"
            "\n"
            "def load():\n"
            "    return importlib.import_module('sigil.integrations.providers.public_execution')\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "dynamic-import"

    def test_dunder_import_rejected(self) -> None:
        source = "module = __import__('sigil.integrations.providers.public_execution')\n"
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "dynamic-import"

    def test_getattr_registry_lookup_rejected(self) -> None:
        source = (
            "import sigil.integrations.providers as providers\n"
            "\n"
            "def resolve(name):\n"
            "    return getattr(providers, 'PublicEquityExecutionProvider')\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "dynamic-attribute"

    def test_registry_setattr_rejected(self) -> None:
        source = (
            "def register(registry, provider_cls):\n"
            "    setattr(registry, 'PublicEquityExecutionProvider', provider_cls)\n"
        )
        violations = guard.scan_file(PRODUCTION_PATH, source)
        assert violations
        assert violations[0].kind == "dynamic-attribute"


class TestTestOnlyImporterIsAllowed:
    def test_test_file_importing_execution_class_is_allowed(self) -> None:
        source = (
            "from sigil.integrations.providers.public_execution import (\n"
            "    PublicEquityExecutionProvider,\n"
            ")\n"
        )
        violations = guard.scan_file(TEST_PATH, source)
        assert violations == []

    def test_real_focused_isolation_tests_are_treated_as_test_files(self) -> None:
        real_test = (
            Path(__file__).resolve().parent
            / "test_governed_public_equity_trading_execution.py"
        )
        assert guard.is_test_path(real_test)


class TestLegitimateReadOnlyReuseIsAllowed:
    """Portfolio state/models and the execution journal already import
    read-only data types (proposals, approvals, normalization helpers, the
    read-only transport) from public_execution. That reuse has no order
    submission capability and must keep passing.
    """

    def test_data_type_imports_are_not_forbidden_names(self) -> None:
        safe_names = [
            "PublicAccessTokenManager",
            "_PublicGovernedTransport",
            "PublicTransportResult",
            "GovernedEquityTradeProposal",
            "GovernedTradeApproval",
            "PublicAuditEvidence",
            "PublicCancellationApproval",
            "PublicExecutionState",
            "PublicOrderExecution",
            "PublicPortfolioSnapshot",
            "PublicPreflightRecord",
            "PublicSubmissionIntent",
            "PublicExecutionPolicy",
            "PublicExecutionJournal",
            "PublicExecutionHealth",
            "normalize_public_account_id",
            "normalize_public_symbol",
            "protected_account_binding",
        ]
        for name in safe_names:
            assert not guard.is_forbidden_name(name), name

    def test_known_production_reusers_pass_individually(self) -> None:
        for relative in (
            "sigil/portfolio/state.py",
            "sigil/portfolio/models.py",
            "sigil/execution/journal.py",
        ):
            path = SIGIL_SRC / relative
            assert path.is_file(), path
            assert guard.scan_file(path) == []


class TestCliEntryPoint:
    def test_main_returns_zero_on_clean_tree(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert guard.main([]) == 0
        assert "OK" in capsys.readouterr().out

    def test_main_reports_violations_and_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_violation = guard.Violation(
            path=PRODUCTION_PATH, line=1, kind="from-import", detail="synthetic"
        )
        monkeypatch.setattr(guard, "scan_tree", lambda root=guard.SRC_ROOT: [fake_violation])
        assert guard.main([]) == 1
        err = capsys.readouterr().err
        assert "FAILED" in err
        assert "synthetic" in err
