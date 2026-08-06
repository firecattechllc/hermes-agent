"""Real, disabled-by-default, read-only Obsidian vault ingestion.

Hermes add-on Phase I. Implements the "Optional read-only Obsidian
integration" roadmap entry: safe Markdown scanning, wikilinks, aliases,
tags, and Web Clipper-style frontmatter, content hashing, and a simple
link-graph -- read-only, with path containment and symlink-escape
prevention, and no plugin/template/embed execution of any kind.

Unrelated to, and must not be confused with, the already-shipped generic
``skills/note-taking/obsidian/`` skill -- this module is a real vault
ingestion pipeline, not a skill definition.

Frontmatter is parsed with ``yaml.safe_load`` only (never ``yaml.load``),
so a malicious ``!!python/object`` tag or similar cannot execute code --
worst case a parse error, handled as "no frontmatter" rather than raised.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

OBSIDIAN_INGESTION_SCHEMA_VERSION = 1

_MAX_FILE_BYTES_DEFAULT = 2_000_000
_MAX_FILES_DEFAULT = 20_000
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_INLINE_TAG = re.compile(r"(?<![#\w])#([A-Za-z0-9_/-]+)")
_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")


class ObsidianIngestionValidationError(ValueError):
    """An Obsidian ingestion operation failed closed."""


@dataclass(frozen=True, slots=True)
class ObsidianIngestionConfig:
    enabled: bool = False
    max_files: int = _MAX_FILES_DEFAULT
    max_file_bytes: int = _MAX_FILE_BYTES_DEFAULT
    schema_version: int = OBSIDIAN_INGESTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OBSIDIAN_INGESTION_SCHEMA_VERSION:
            raise ObsidianIngestionValidationError(
                "unsupported Obsidian ingestion config schema"
            )
        if not 1 <= self.max_files <= 200_000:
            raise ObsidianIngestionValidationError("max_files is outside bounds")
        if not 1_000 <= self.max_file_bytes <= 20_000_000:
            raise ObsidianIngestionValidationError("max_file_bytes is outside bounds")

    @property
    def can_write(self) -> bool:
        return False

    @property
    def can_execute_templates(self) -> bool:
        return False

    @property
    def can_execute_plugins(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ObsidianNote:
    relative_path: str
    title: str
    content_digest: str
    byte_size: int
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    wikilinks: tuple[str, ...]
    frontmatter: dict[str, Any]
    excerpt: str


@dataclass(frozen=True, slots=True)
class ObsidianVaultSnapshot:
    root: str
    notes: tuple[ObsidianNote, ...] = field(default_factory=tuple)
    skipped_paths: tuple[str, ...] = field(default_factory=tuple)
    rejected_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def note_count(self) -> int:
        return len(self.notes)

    def link_graph(self) -> dict[str, tuple[str, ...]]:
        """Resolve each note's wikilinks to relative paths of notes in this vault.

        A wikilink target that doesn't match any ingested note (an external
        reference, a typo, or a note outside the vault) is simply omitted
        from that note's resolved edges -- it is never treated as license
        to look outside the vault root.
        """

        by_stem: dict[str, str] = {}
        for note in self.notes:
            stem = Path(note.relative_path).stem.lower()
            by_stem.setdefault(stem, note.relative_path)

        graph: dict[str, tuple[str, ...]] = {}
        for note in self.notes:
            resolved = tuple(
                dict.fromkeys(
                    by_stem[link.lower()]
                    for link in note.wikilinks
                    if link.lower() in by_stem
                )
            )
            graph[note.relative_path] = resolved
        return graph


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _require_enabled(config: ObsidianIngestionConfig) -> None:
    if not config.enabled:
        raise ObsidianIngestionValidationError(
            "Obsidian vault ingestion is disabled by policy"
        )


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split leading YAML frontmatter from the body, parsed with safe_load only.

    A malformed or unsafe frontmatter block degrades to ``{}`` (treated as
    absent) rather than raising, so one bad note never aborts a whole vault
    ingest.
    """

    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text

    body = text[match.end() :]
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, body

    return (parsed if isinstance(parsed, dict) else {}), body


def _extract_title(body: str, frontmatter: dict[str, Any], fallback: str) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:200]

    heading = _HEADING.search(body)
    if heading:
        return heading.group(1).strip()

    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]

    return fallback


def _normalize_string_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [part.strip() for part in re.split(r"[,\n]", value)]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        return ()
    return tuple(dict.fromkeys(item for item in items if item))


def _contained_markdown_files(
    root: Path, config: ObsidianIngestionConfig
) -> tuple[list[Path], list[str]]:
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ObsidianIngestionValidationError("vault root must be an existing directory")

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
        if any(part.startswith(".obsidian") for part in resolved_candidate.parts):
            continue  # Obsidian's own config/plugin directory: never ingested.

        accepted.append(resolved_candidate)
        if len(accepted) >= config.max_files:
            break

    return accepted, rejected


def ingest_vault(root: str | Path, config: ObsidianIngestionConfig) -> ObsidianVaultSnapshot:
    """Ingest every path-contained ``*.md`` note under an Obsidian vault root.

    Read-only and inert: no file is written, no Obsidian plugin, template,
    dataview query, or embed directive is executed. The vault's own
    ``.obsidian/`` configuration directory (which can contain plugin code)
    is explicitly excluded from ingestion, not merely from execution.
    """

    _require_enabled(config)

    root_path = Path(root)
    accepted_paths, rejected_paths = _contained_markdown_files(root_path, config)
    resolved_root = root_path.resolve(strict=True)

    notes: list[ObsidianNote] = []
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
        frontmatter, body = _parse_frontmatter(text)

        wikilinks = tuple(
            dict.fromkeys(target.strip() for target, _alias in _WIKILINK.findall(body))
        )
        inline_tags = tuple(dict.fromkeys(_INLINE_TAG.findall(body)))
        frontmatter_tags = _normalize_string_list(frontmatter.get("tags"))
        tags = tuple(dict.fromkeys((*frontmatter_tags, *inline_tags)))
        aliases = _normalize_string_list(frontmatter.get("aliases"))

        sanitized_frontmatter = {
            key: value
            for key, value in frontmatter.items()
            if isinstance(key, str) and isinstance(value, (str, int, float, bool, list))
        }

        notes.append(
            ObsidianNote(
                relative_path=relative,
                title=_extract_title(body, frontmatter, fallback=path.stem),
                content_digest=_digest(raw),
                byte_size=len(raw),
                tags=tags,
                aliases=aliases,
                wikilinks=wikilinks,
                frontmatter=sanitized_frontmatter,
                excerpt=body.strip()[:500],
            )
        )

    return ObsidianVaultSnapshot(
        root=str(resolved_root),
        notes=tuple(notes),
        skipped_paths=tuple(skipped),
        rejected_paths=tuple(rejected_paths),
    )
