from __future__ import annotations

from pathlib import Path

from hermes_docs_worker.wikilinks import (
    WikiLinkIndex,
    extract_wikilinks,
    validate_generated_content,
)


def test_extract_wikilinks_finds_all_links() -> None:
    text = "See [[Fleet Status]] and [[Other Note|display text]] and [[Third#heading]]."
    assert extract_wikilinks(text) == ("Fleet Status", "Other Note", "Third")


def test_index_resolves_existing_note(tmp_path: Path) -> None:
    (tmp_path / "Fleet Status.md").write_text("content", encoding="utf-8")
    index = WikiLinkIndex(tmp_path)
    assert index.resolves("Fleet Status")
    assert index.resolves("fleet status")  # case-insensitive
    assert not index.resolves("Nonexistent")


def test_validate_generated_content_flags_broken_links(tmp_path: Path) -> None:
    broken = validate_generated_content(
        {Path("a.md"): "links to [[Nonexistent Note]]"}, tmp_path
    )
    assert broken[Path("a.md")] == ("Nonexistent Note",)


def test_validate_generated_content_allows_links_between_files_in_the_same_run(
    tmp_path: Path,
) -> None:
    documents = {
        Path("a.md"): "see [[b]]",
        Path("b.md"): "see [[a]]",
    }
    broken = validate_generated_content(documents, tmp_path)
    assert broken == {}


def test_validate_generated_content_allows_links_to_existing_vault_notes(
    tmp_path: Path,
) -> None:
    (tmp_path / "Existing.md").write_text("content", encoding="utf-8")
    broken = validate_generated_content({Path("a.md"): "see [[Existing]]"}, tmp_path)
    assert broken == {}
