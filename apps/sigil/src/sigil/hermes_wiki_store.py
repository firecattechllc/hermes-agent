"""Real, disabled-by-default local document store for Hermes Wiki.

Hermes add-on Phase G. Builds the real content store and ingestion worker
that ``sigil.hermes_wiki_adapter`` (a Stage 7 evaluation/evidence contract,
not a transport) has no backing for. Scoped intentionally narrow: a
read-only, path-contained local Markdown directory, not a network crawler
or external wiki client -- that keeps this module free of any new network
attack surface while still being a real, working store rather than another
disabled stub.

Path containment: every ingested file must resolve (after following
symlinks) to a path underneath the configured root. A symlink or ``..``
segment that would escape the root is rejected rather than silently
followed, so a malicious or mistaken symlink inside a wiki root can never
be used to read files elsewhere on disk.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERMES_WIKI_STORE_SCHEMA_VERSION = 1

_MAX_FILE_BYTES = 2_000_000
_MAX_FILES_DEFAULT = 5_000
_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


class HermesWikiStoreValidationError(ValueError):
    """A Hermes Wiki store operation failed closed."""


@dataclass(frozen=True, slots=True)
class WikiStoreConfig:
    enabled: bool = False
    max_files: int = _MAX_FILES_DEFAULT
    max_file_bytes: int = _MAX_FILE_BYTES
    schema_version: int = HERMES_WIKI_STORE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HERMES_WIKI_STORE_SCHEMA_VERSION:
            raise HermesWikiStoreValidationError("unsupported Hermes Wiki store schema")
        if not 1 <= self.max_files <= 100_000:
            raise HermesWikiStoreValidationError("max_files is outside bounds")
        if not 1_000 <= self.max_file_bytes <= 20_000_000:
            raise HermesWikiStoreValidationError("max_file_bytes is outside bounds")

    @property
    def can_write(self) -> bool:
        return False

    @property
    def can_execute_plugins(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class StoredWikiDocument:
    document_id: str
    relative_path: str
    title: str
    content_digest: str
    byte_size: int
    heading_count: int
    wikilinks: tuple[str, ...]
    markdown_links: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True, slots=True)
class WikiIngestResult:
    root: str
    documents: tuple[StoredWikiDocument, ...] = field(default_factory=tuple)
    skipped_paths: tuple[str, ...] = field(default_factory=tuple)
    rejected_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def document_count(self) -> int:
        return len(self.documents)


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _document_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]


def _extract_title(text: str, fallback: str) -> str:
    match = _HEADING.search(text)
    if match:
        return match.group(1).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return fallback


def _require_enabled(config: WikiStoreConfig) -> None:
    if not config.enabled:
        raise HermesWikiStoreValidationError(
            "Hermes Wiki local store is disabled by policy"
        )


def _contained_markdown_files(root: Path, config: WikiStoreConfig) -> tuple[list[Path], list[str]]:
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise HermesWikiStoreValidationError("wiki root must be an existing directory")

    accepted: list[Path] = []
    rejected: list[str] = []

    for candidate in sorted(resolved_root.rglob("*.md")):
        try:
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            rejected.append(str(candidate))
            continue

        if not resolved_candidate.is_file():
            continue

        accepted.append(resolved_candidate)
        if len(accepted) >= config.max_files:
            break

    return accepted, rejected


def ingest_directory(root: str | Path, config: WikiStoreConfig) -> WikiIngestResult:
    """Ingest every path-contained ``*.md`` file under ``root``.

    Read-only: no file is written, moved, or deleted. No Markdown plugin,
    template, or embed directive is executed -- files are read as inert
    text and only regex-scanned for headings and link syntax.
    """

    _require_enabled(config)

    root_path = Path(root)
    accepted_paths, rejected_paths = _contained_markdown_files(root_path, config)
    resolved_root = root_path.resolve(strict=True)

    documents: list[StoredWikiDocument] = []
    skipped: list[str] = []

    for path in accepted_paths:
        relative = str(path.relative_to(resolved_root))
        try:
            raw = path.read_bytes()
        except OSError:
            skipped.append(relative)
            continue

        if len(raw) > config.max_file_bytes:
            skipped.append(relative)
            continue

        text = raw.decode("utf-8", errors="replace")
        wikilinks = tuple(dict.fromkeys(match.strip() for match in _WIKILINK.findall(text)))
        markdown_links = tuple(
            dict.fromkeys(match.strip() for match in _MARKDOWN_LINK.findall(text))
        )
        headings = _HEADING.findall(text)

        documents.append(
            StoredWikiDocument(
                document_id=_document_id(relative),
                relative_path=relative,
                title=_extract_title(text, fallback=path.stem),
                content_digest=_digest(raw),
                byte_size=len(raw),
                heading_count=len(headings),
                wikilinks=wikilinks,
                markdown_links=markdown_links,
                excerpt=text.strip()[:500],
            )
        )

    return WikiIngestResult(
        root=str(resolved_root),
        documents=tuple(documents),
        skipped_paths=tuple(skipped),
        rejected_paths=tuple(rejected_paths),
    )


def search_documents(result: WikiIngestResult, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Simple case-insensitive substring search over an ingested result set.

    No network call, no external index; only ever searches documents this
    process already ingested from disk in :func:`ingest_directory`.
    """

    if not 1 <= limit <= 500:
        raise HermesWikiStoreValidationError("search limit is outside bounds")

    normalized = query.strip().lower()
    if not normalized:
        return []

    matches = [
        {
            "document_id": document.document_id,
            "relative_path": document.relative_path,
            "title": document.title,
            "excerpt": document.excerpt,
        }
        for document in result.documents
        if normalized in document.title.lower() or normalized in document.excerpt.lower()
    ]

    return matches[:limit]
