from __future__ import annotations

from pathlib import Path

from hermes_docs_worker.collectors import vault_contradictions


def test_no_incidents_dir_returns_empty(tmp_path: Path) -> None:
    assert vault_contradictions.collect(tmp_path) == ()


def test_unresolved_incident_is_surfaced(tmp_path: Path) -> None:
    incidents = tmp_path / "00-Inbox" / "incidents"
    incidents.mkdir(parents=True)
    (incidents / "x.md").write_text(
        '---\nstatus: unresolved\ntitle: "Disk usage discrepancy"\n---\n\nBody.\n', encoding="utf-8"
    )
    items = vault_contradictions.collect(tmp_path)
    assert len(items) == 1
    assert items[0].title == "Disk usage discrepancy"


def test_resolved_incident_is_not_surfaced(tmp_path: Path) -> None:
    incidents = tmp_path / "00-Inbox" / "incidents"
    incidents.mkdir(parents=True)
    (incidents / "x.md").write_text("---\nstatus: resolved\n---\n\nBody.\n", encoding="utf-8")
    assert vault_contradictions.collect(tmp_path) == ()


def test_malformed_frontmatter_is_skipped_not_a_crash(tmp_path: Path) -> None:
    incidents = tmp_path / "00-Inbox" / "incidents"
    incidents.mkdir(parents=True)
    (incidents / "x.md").write_text("no frontmatter here at all", encoding="utf-8")
    assert vault_contradictions.collect(tmp_path) == ()


def test_vault_content_is_never_treated_as_instructions() -> None:
    """The frontmatter parser only ever extracts key: value pairs -- a note
    body containing something that looks like a shell command or a
    directive must never influence what this collector does."""
    import inspect

    source = inspect.getsource(vault_contradictions)
    assert "subprocess" not in source
    assert "eval(" not in source
    assert "exec(" not in source
