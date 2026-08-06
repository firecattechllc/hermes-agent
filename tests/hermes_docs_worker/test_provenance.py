from __future__ import annotations

from hermes_docs_worker.evidence import EvidenceFact
from hermes_docs_worker.provenance import (
    AUTO_BLOCK_BEGIN,
    AUTO_BLOCK_END,
    provenance_entries_from_facts,
    render_provenance_footer,
    render_source_provenance_update,
)
from hermes_docs_worker.status import StatusValue


def _fact(source: str, category: str = "c") -> EvidenceFact:
    return EvidenceFact(
        category=category, label="l", status=StatusValue.VERIFIED, source=source, collected_at=0,
    )


def test_provenance_entries_group_by_source_and_category() -> None:
    entries = provenance_entries_from_facts((_fact("a"), _fact("a"), _fact("b")))
    by_source = {e.source: e.fact_count for e in entries}
    assert by_source == {"a": 2, "b": 1}


def test_render_provenance_footer_lists_sources() -> None:
    footer = render_provenance_footer(
        provenance_entries_from_facts((_fact("system_health"),)), generated_at_iso="2026-01-01T00:00:00Z"
    )
    assert "system_health" in footer
    assert "Provenance" in footer


def test_source_provenance_update_creates_block_when_absent() -> None:
    updated = render_source_provenance_update(
        "", (), generated_at_iso="2026-01-01T00:00:00Z", run_id="r1"
    )
    assert AUTO_BLOCK_BEGIN in updated
    assert AUTO_BLOCK_END in updated


def test_source_provenance_update_preserves_human_content_outside_block() -> None:
    previous = "# Source Provenance\n\nHuman-written notes that must survive.\n"
    updated = render_source_provenance_update(
        previous, (), generated_at_iso="2026-01-01T00:00:00Z", run_id="r1"
    )
    assert "Human-written notes that must survive." in updated


def test_source_provenance_update_replaces_only_the_auto_block_on_rerun() -> None:
    previous = (
        "# Source Provenance\n\nHuman notes.\n\n"
        f"{AUTO_BLOCK_BEGIN}\nstale content\n{AUTO_BLOCK_END}\n\nMore human notes.\n"
    )
    updated = render_source_provenance_update(
        previous, (), generated_at_iso="2026-01-01T00:00:00Z", run_id="r2"
    )
    assert "Human notes." in updated
    assert "More human notes." in updated
    assert "stale content" not in updated
    assert "r2" in updated
