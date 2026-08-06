"""Wiki-link validation.

Obsidian ``[[Note Name]]`` links are validated against an index of the
vault's actual notes before anything is written, so this worker never ships
a broken link into the vault. This module only ever *reads* the vault to
build the index and *reports* broken links -- it never treats vault content
as instructions (see the module docstring in
:mod:`hermes_docs_worker.collectors.vault_contradictions` for the same
principle applied to contradiction scanning).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence

_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def extract_wikilinks(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in _WIKILINK_PATTERN.finditer(text))


def _normalize(name: str) -> str:
    return name.strip().lower()


class WikiLinkIndex:
    """Maps a normalized note name to its vault-relative path."""

    def __init__(self, vault_root: Path) -> None:
        self._by_name: dict[str, Path] = {}
        if not vault_root.exists():
            return
        for path in vault_root.rglob("*.md"):
            relative = path.relative_to(vault_root)
            self._by_name[_normalize(path.stem)] = relative

    def resolves(self, name: str) -> bool:
        return _normalize(name) in self._by_name

    def add_pending(self, relative_paths: Sequence[Path]) -> None:
        """Register files this same run is about to create, so a link from
        one generated document to another generated document validates
        even though neither exists on disk yet."""
        for path in relative_paths:
            self._by_name[_normalize(Path(path).stem)] = Path(path)


def validate_generated_content(
    paths_to_content: Mapping[Path, str], vault_root: Path
) -> dict[Path, tuple[str, ...]]:
    """Broken wiki-link targets per generated file. An empty tuple means
    every link in that file resolves."""
    index = WikiLinkIndex(vault_root)
    index.add_pending(list(paths_to_content.keys()))

    broken: dict[Path, tuple[str, ...]] = {}
    for relative_path, content in paths_to_content.items():
        missing = tuple(
            link for link in extract_wikilinks(content) if not index.resolves(link)
        )
        if missing:
            broken[relative_path] = missing
    return broken
