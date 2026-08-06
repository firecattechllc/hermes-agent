"""Unresolved contradictions already recorded in the documentation vault.

Read-only frontmatter scan of ``00-Inbox/incidents/*.md``: a note with
``status: unresolved`` (or ``open``) in its YAML frontmatter is surfaced as
an existing :class:`VaultContradiction`. This module parses only the
frontmatter block as data -- never as instructions, and never as Markdown
structure to render verbatim -- so a vault note cannot inject a directive
this worker would act on. Malformed frontmatter is skipped, never treated
as an error that could abort collection.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from hermes_docs_worker.contradiction import VaultContradiction

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_UNRESOLVED_STATUSES = {"unresolved", "open"}


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip().lower()] = value.strip().strip('"').strip("'")
    return fields


def collect(vault_root: Path) -> Tuple[VaultContradiction, ...]:
    incidents_dir = vault_root / "00-Inbox" / "incidents"
    if not incidents_dir.exists():
        return ()

    items: list[VaultContradiction] = []
    for path in sorted(incidents_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields = _parse_frontmatter(text)
        status = fields.get("status", "").lower()
        if status not in _UNRESOLVED_STATUSES:
            continue
        title = fields.get("title") or path.stem
        items.append(
            VaultContradiction(
                vault_path=str(path.relative_to(vault_root)), title=title[:256],
            )
        )
    return tuple(items)
