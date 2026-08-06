from __future__ import annotations

import pytest

from sigil.obsidian_ingestion import (
    ObsidianIngestionConfig,
    ObsidianIngestionValidationError,
    ingest_vault,
)

ENABLED = ObsidianIngestionConfig(enabled=True)
DISABLED = ObsidianIngestionConfig(enabled=False)


def test_disabled_config_rejects_ingestion(tmp_path) -> None:
    with pytest.raises(ObsidianIngestionValidationError, match="disabled"):
        ingest_vault(tmp_path, DISABLED)


def test_ingests_markdown_with_frontmatter_tags_and_wikilinks(tmp_path) -> None:
    (tmp_path / "note.md").write_text(
        "---\n"
        "title: My Note\n"
        "tags: [project, urgent]\n"
        "aliases: [MN, My Note Alias]\n"
        "---\n"
        "# My Note\n\n"
        "See [[Other Note]] and [[Other Note|a friendly name]]. Also #inline-tag.\n"
    )
    (tmp_path / "other-note.md").write_text("# Other Note\n\nSome content.")

    result = ingest_vault(tmp_path, ENABLED)

    assert result.note_count == 2
    note = next(n for n in result.notes if n.relative_path == "note.md")
    assert note.title == "My Note"
    assert set(note.tags) == {"project", "urgent", "inline-tag"}
    assert note.aliases == ("MN", "My Note Alias")
    assert note.wikilinks == ("Other Note",)
    assert note.frontmatter["title"] == "My Note"


def test_link_graph_resolves_wikilinks_to_vault_notes(tmp_path) -> None:
    (tmp_path / "a.md").write_text("# A\n\nLinks to [[B]].")
    (tmp_path / "b.md").write_text("# B\n\nNo links.")

    result = ingest_vault(tmp_path, ENABLED)
    graph = result.link_graph()

    assert graph["a.md"] == ("b.md",)
    assert graph["b.md"] == ()


def test_link_graph_ignores_links_outside_the_vault(tmp_path) -> None:
    (tmp_path / "a.md").write_text("# A\n\nLinks to [[Nonexistent Note]].")

    result = ingest_vault(tmp_path, ENABLED)

    assert result.link_graph()["a.md"] == ()


def test_dotobsidian_directory_is_excluded(tmp_path) -> None:
    obsidian_dir = tmp_path / ".obsidian" / "plugins" / "some-plugin"
    obsidian_dir.mkdir(parents=True)
    (obsidian_dir / "data.md").write_text("# plugin config disguised as markdown")
    (tmp_path / "real-note.md").write_text("# Real Note")

    result = ingest_vault(tmp_path, ENABLED)

    relative_paths = {note.relative_path for note in result.notes}
    assert relative_paths == {"real-note.md"}


def test_rejects_symlink_escape_outside_vault_root(tmp_path) -> None:
    outside = tmp_path.parent / "outside-private-note.md"
    outside.write_text("# Private\n\nshould never be read")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "legit.md").write_text("# Legit")

    escape = vault / "escape.md"
    try:
        escape.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    result = ingest_vault(vault, ENABLED)

    relative_paths = {note.relative_path for note in result.notes}
    assert "escape.md" not in relative_paths
    assert not any("Private" in note.excerpt for note in result.notes)


def test_malicious_yaml_frontmatter_never_executes_and_degrades_safely(tmp_path) -> None:
    # A python-object tag is exactly what yaml.load (not safe_load) would
    # execute; safe_load must refuse it and this must degrade to "no
    # frontmatter" rather than raising or executing anything.
    (tmp_path / "evil.md").write_text(
        "---\n"
        "run: !!python/object/apply:os.system [\"echo pwned\"]\n"
        "---\n"
        "# Evil\n\nBody text.\n"
    )

    result = ingest_vault(tmp_path, ENABLED)

    assert result.note_count == 1
    note = result.notes[0]
    assert note.frontmatter == {}
    assert note.title == "Evil"


def test_oversized_note_is_skipped_not_crashed(tmp_path) -> None:
    small_config = ObsidianIngestionConfig(enabled=True, max_file_bytes=1_000)
    (tmp_path / "big.md").write_text("# Big\n\n" + ("x" * 2_000))

    result = ingest_vault(tmp_path, small_config)

    assert result.note_count == 0
    assert "big.md" in result.skipped_paths


def test_content_digest_is_deterministic(tmp_path) -> None:
    (tmp_path / "a.md").write_text("# A\n\nStable content.")

    first = ingest_vault(tmp_path, ENABLED)
    second = ingest_vault(tmp_path, ENABLED)

    assert first.notes[0].content_digest == second.notes[0].content_digest
    assert first.notes[0].content_digest.startswith("sha256:")


def test_frontmatter_never_leaks_non_scalar_or_nested_structures(tmp_path) -> None:
    (tmp_path / "note.md").write_text(
        "---\n"
        "title: Note\n"
        "nested:\n"
        "  secret: value\n"
        "---\n"
        "# Note\n"
    )

    result = ingest_vault(tmp_path, ENABLED)

    assert "nested" not in result.notes[0].frontmatter
