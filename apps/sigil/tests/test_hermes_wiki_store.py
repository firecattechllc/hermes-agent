from __future__ import annotations

import os

import pytest

from sigil.hermes_wiki_store import (
    HermesWikiStoreValidationError,
    WikiStoreConfig,
    ingest_directory,
    search_documents,
)

ENABLED = WikiStoreConfig(enabled=True)
DISABLED = WikiStoreConfig(enabled=False)


def test_disabled_store_rejects_ingestion(tmp_path) -> None:
    with pytest.raises(HermesWikiStoreValidationError, match="disabled"):
        ingest_directory(tmp_path, DISABLED)


def test_ingests_real_markdown_files(tmp_path) -> None:
    (tmp_path / "one.md").write_text("# Hello World\n\nSome [[Wikilink]] and a [link](https://example.com).")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "two.md").write_text("# Second Doc\n\nMore text here.")
    (tmp_path / "ignored.txt").write_text("not markdown")

    result = ingest_directory(tmp_path, ENABLED)

    assert result.document_count == 2
    relative_paths = {doc.relative_path for doc in result.documents}
    assert relative_paths == {"one.md", os.path.join("sub", "two.md")}

    one = next(doc for doc in result.documents if doc.relative_path == "one.md")
    assert one.title == "Hello World"
    assert one.wikilinks == ("Wikilink",)
    assert one.markdown_links == ("https://example.com",)
    assert one.content_digest.startswith("sha256:")


def test_ingestion_is_deterministic_and_content_addressed(tmp_path) -> None:
    (tmp_path / "a.md").write_text("# A\n\nbody")

    first = ingest_directory(tmp_path, ENABLED)
    second = ingest_directory(tmp_path, ENABLED)

    assert first.documents[0].content_digest == second.documents[0].content_digest
    assert first.documents[0].document_id == second.documents[0].document_id


def test_rejects_symlink_escape_outside_root(tmp_path) -> None:
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("# Secret\n\nshould never be read")
    root = tmp_path / "vault"
    root.mkdir()
    (root / "legit.md").write_text("# Legit\n\nfine")

    escape_link = root / "escape.md"
    try:
        escape_link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    result = ingest_directory(root, ENABLED)

    relative_paths = {doc.relative_path for doc in result.documents}
    assert "legit.md" in relative_paths
    assert "escape.md" not in relative_paths
    assert not any("Secret" in doc.excerpt for doc in result.documents)


def test_rejects_nonexistent_root(tmp_path) -> None:
    with pytest.raises((HermesWikiStoreValidationError, FileNotFoundError, OSError)):
        ingest_directory(tmp_path / "does-not-exist", ENABLED)


def test_oversized_file_is_skipped_not_crashed(tmp_path) -> None:
    small_config = WikiStoreConfig(enabled=True, max_file_bytes=1_000)
    big = tmp_path / "big.md"
    big.write_text("# Big\n\n" + ("x" * 2_000))

    result = ingest_directory(tmp_path, small_config)

    assert result.document_count == 0
    assert "big.md" in result.skipped_paths


def test_max_files_bound_is_enforced(tmp_path) -> None:
    for index in range(5):
        (tmp_path / f"doc-{index}.md").write_text(f"# Doc {index}")

    limited_config = WikiStoreConfig(enabled=True, max_files=2)
    result = ingest_directory(tmp_path, limited_config)

    assert result.document_count == 2


def test_search_matches_title_and_excerpt(tmp_path) -> None:
    (tmp_path / "alpha.md").write_text("# Alpha Project\n\nDetails about the alpha rollout.")
    (tmp_path / "beta.md").write_text("# Beta Project\n\nUnrelated content.")

    result = ingest_directory(tmp_path, ENABLED)
    matches = search_documents(result, "alpha")

    assert len(matches) == 1
    assert matches[0]["relative_path"] == "alpha.md"


def test_search_rejects_out_of_bounds_limit(tmp_path) -> None:
    result = ingest_directory(tmp_path, ENABLED)

    with pytest.raises(HermesWikiStoreValidationError, match="limit"):
        search_documents(result, "x", limit=0)


def test_search_blank_query_returns_nothing(tmp_path) -> None:
    (tmp_path / "a.md").write_text("# A")
    result = ingest_directory(tmp_path, ENABLED)

    assert search_documents(result, "   ") == []
