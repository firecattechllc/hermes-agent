#!/usr/bin/env python3
"""Fail-closed guard: Sigil's real-money Public.com execution client must stay
unreachable from production code.

``sigil.integrations.providers.public_execution`` implements a governed but
live-capable equity execution workflow (``PublicEquityExecutionProvider``).
It has no production caller today. This script is the enforcement mechanism
that keeps it that way until a future, separately certified live-trading
change deliberately replaces this guard.

It is deliberately narrower than "nothing may import anything from
public_execution": several production modules (portfolio state acquisition,
portfolio models, the execution journal) already import read-only data types,
normalization helpers, and the read-only Public transport from that module for
legitimate, non-submitting purposes, and that is unaffected. What must stay
unreachable is the *order-submission* surface -- the
``PublicEquityExecutionProvider`` class (and any equivalent execution class or
factory) -- whether reached by a direct import, a from-import, the
``sigil.integrations.providers`` package re-export, a dynamic
``importlib``/``getattr`` lookup, or a registry/factory registration.

Run directly:

    uv run --with pytest python scripts/verify_public_execution_isolation.py

Exits 0 with no output when the repository is clean, or prints one line per
violation and exits 1.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SIGIL_ROOT = Path(__file__).resolve().parents[1]  # apps/sigil
SRC_ROOT = SIGIL_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "sigil"

EXECUTION_MODULE = "sigil.integrations.providers.public_execution"
PACKAGE_GATEWAY_MODULE = "sigil.integrations.providers"

# The provider's own implementation boundary. These files necessarily define
# and re-export the execution surface; excluding them is what lets the guard
# target *reachability from other code* instead of the definition itself.
EXCLUDED_FILES = frozenset(
    {
        (PACKAGE_ROOT / "integrations" / "providers" / "public_execution.py").resolve(),
        (PACKAGE_ROOT / "integrations" / "providers" / "__init__.py").resolve(),
    }
)

# Dedicated guard implementation files are exempt from scanning themselves --
# they necessarily reference the forbidden names in order to detect them.
EXCLUDED_FILES |= {
    Path(__file__).resolve(),
}

# The one class today capable of submitting/cancelling real Public.com
# orders. ``PublicExecutionProvider`` is kept as an alias in case a future
# rename drops the "Equity" qualifier.
FORBIDDEN_EXACT_NAMES = frozenset(
    {
        "PublicEquityExecutionProvider",
        "PublicExecutionProvider",
    }
)
# Catches future equivalents (factories, alternate execution clients) by
# shape rather than by an ever-growing exact-name list. Deliberately does not
# match the read-only/data types already used in production (e.g.
# PublicExecutionState, PublicExecutionPolicy, PublicExecutionJournal,
# PublicOrderExecution) because none of those end in Provider/Factory/Client.
FORBIDDEN_NAME_PATTERN = re.compile(r"^Public\w*Execution\w*(Provider|Factory|Client)$")

_DYNAMIC_IMPORT_ATTRS = frozenset({"import_module"})
_DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "import_module"})
_DYNAMIC_ATTRIBUTE_CALL_NAMES = frozenset({"getattr", "setattr", "hasattr"})


def is_forbidden_name(name: str) -> bool:
    return name in FORBIDDEN_EXACT_NAMES or bool(FORBIDDEN_NAME_PATTERN.match(name))


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    kind: str
    detail: str

    def __str__(self) -> str:
        try:
            rel = self.path.resolve().relative_to(SIGIL_ROOT.resolve().parents[1])
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line}: [{self.kind}] {self.detail}"


def is_test_path(path: Path) -> bool:
    parts = path.parts
    name = path.name
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def production_python_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and p.resolve() not in EXCLUDED_FILES
    )


def _module_and_package(path: Path) -> tuple[str, str] | None:
    """Return (dotted module name, __package__) for a file inside PACKAGE_ROOT."""
    try:
        relative = path.resolve().relative_to(PACKAGE_ROOT.resolve())
    except ValueError:
        return None
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        module_name = ".".join(["sigil", *parts[:-1]])
        return module_name, module_name
    parts[-1] = parts[-1].removesuffix(".py")
    module_name = ".".join(["sigil", *parts])
    package = ".".join(module_name.split(".")[:-1])
    return module_name, package


def _resolve_relative(containing_package: str, level: int, module: str | None) -> str | None:
    bits = containing_package.rsplit(".", level - 1) if level > 1 else [containing_package]
    if not bits or not bits[0]:
        return None
    base = bits[0]
    return f"{base}.{module}" if module else base


class _FileScanner:
    def __init__(self, path: Path, tree: ast.AST, module_info: tuple[str, str] | None) -> None:
        self.path = path
        self.tree = tree
        self.module_name, self.package = module_info or ("", "")
        self.violations: list[Violation] = []
        # local binding name -> resolved absolute dotted module it refers to
        self.module_aliases: dict[str, str] = {}

    def run(self) -> list[Violation]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                self._visit_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._visit_import_from(node)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute):
                self._visit_attribute(node)
            elif isinstance(node, ast.Call):
                self._visit_call(node)
        return self.violations

    def _flag(self, node: ast.AST, kind: str, detail: str) -> None:
        self.violations.append(
            Violation(path=self.path, line=getattr(node, "lineno", 0), kind=kind, detail=detail)
        )

    def _visit_import(self, node: ast.Import) -> None:
        for alias in node.names:
            resolved = alias.name
            local = (alias.asname or alias.name).split(".")[0]
            if resolved == EXECUTION_MODULE or resolved.startswith(EXECUTION_MODULE + "."):
                self._flag(
                    node,
                    "direct-import",
                    f"production module imports {resolved!r} (real-money Public execution client)",
                )
                continue
            # Track package/module aliases so later attribute access (e.g.
            # `providers.PublicEquityExecutionProvider`) can be resolved.
            top_level_bound = alias.asname is not None or "." not in alias.name
            if top_level_bound:
                self.module_aliases[local] = resolved

    def _visit_import_from(self, node: ast.ImportFrom) -> None:
        if node.level == 0:
            resolved_module = node.module or ""
        else:
            resolved = _resolve_relative(self.package, node.level, node.module)
            if resolved is None:
                return
            resolved_module = resolved

        is_execution_module = resolved_module == EXECUTION_MODULE
        is_gateway_module = resolved_module == PACKAGE_GATEWAY_MODULE
        if not (is_execution_module or is_gateway_module):
            return

        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name == "*":
                self._flag(
                    node,
                    "wildcard-import",
                    f"production module does `from {resolved_module} import *`, "
                    "which reaches the Public execution client",
                )
                continue
            if is_gateway_module and alias.name == "public_execution":
                # `from sigil.integrations.providers import public_execution`
                # binds the whole submodule -- equivalent to a direct import.
                self._flag(
                    node,
                    "direct-import",
                    "production module imports the public_execution submodule "
                    "via the providers package",
                )
                continue
            if is_forbidden_name(alias.name):
                self._flag(
                    node,
                    "from-import",
                    f"production module imports {alias.name!r} from {resolved_module!r}",
                )
                continue
            # Non-forbidden symbol (e.g. GovernedEquityTradeProposal,
            # PublicAccessTokenManager, normalize_public_symbol) -- allowed.
            del local

    def _resolve_attribute_root(self, node: ast.Attribute) -> str | None:
        value = node.value
        if isinstance(value, ast.Name):
            return self.module_aliases.get(value.id)
        return None

    def _visit_attribute(self, node: ast.Attribute) -> None:
        if not is_forbidden_name(node.attr):
            return
        root_module = self._resolve_attribute_root(node)
        if root_module is None:
            return
        if root_module == EXECUTION_MODULE or root_module == PACKAGE_GATEWAY_MODULE:
            self._flag(
                node,
                "attribute-access",
                f"production module reaches {node.attr!r} via {root_module!r}",
            )

    def _call_func_name(self, node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _string_args(self, node: ast.Call) -> list[str]:
        values: list[str] = []
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                values.append(arg.value)
        return values

    def _visit_call(self, node: ast.Call) -> None:
        func_name = self._call_func_name(node)
        if func_name is None:
            return
        strings = self._string_args(node)

        if func_name in _DYNAMIC_IMPORT_NAMES:
            for value in strings:
                if "public_execution" in value:
                    self._flag(
                        node,
                        "dynamic-import",
                        f"production module dynamically imports {value!r}",
                    )

        if func_name in _DYNAMIC_ATTRIBUTE_CALL_NAMES:
            for value in strings:
                if is_forbidden_name(value):
                    self._flag(
                        node,
                        "dynamic-attribute",
                        f"production module uses {func_name}(..., {value!r}, ...) "
                        "to reach the Public execution client dynamically",
                    )


def scan_file(path: Path, source: str | None = None) -> list[Violation]:
    """Scan a single file for forbidden reachability of the execution client.

    ``path`` need not exist on disk when ``source`` is supplied -- this lets
    tests exercise synthetic production/test files without touching the real
    repository tree. Test files (by path convention) are always skipped and
    return no violations, matching the "focused tests that verify the
    provider remains isolated" exclusion.
    """
    if is_test_path(path):
        return []
    if source is None:
        try:
            source = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(
                path=path,
                line=exc.lineno or 0,
                kind="parse-error",
                detail=f"could not parse for isolation scanning: {exc.msg}",
            )
        ]
    module_info = _module_and_package(path)
    return _FileScanner(path, tree, module_info).run()


def scan_tree(root: Path = SRC_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    for path in production_python_files(root):
        violations.extend(scan_file(path))
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv
    violations = scan_tree()
    if not violations:
        print("public_execution isolation guard: OK -- no production reachability found")
        return 0
    print("public_execution isolation guard: FAILED", file=sys.stderr)
    for violation in violations:
        print(f"  {violation}", file=sys.stderr)
    print(
        "\nThe real-money Public.com execution client "
        f"({EXECUTION_MODULE}.PublicEquityExecutionProvider) must not be reachable "
        "from production code. If this is an intentional, separately certified "
        "live-trading change, update this guard explicitly rather than routing "
        "around it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
