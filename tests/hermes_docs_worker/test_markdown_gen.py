from __future__ import annotations

from hermes_docs_worker import markdown_gen
from hermes_docs_worker.contradiction import Contradiction, VaultContradiction
from hermes_docs_worker.evidence import EvidenceFact, EvidenceSnapshot
from hermes_docs_worker.status import StatusValue


def _fact(label: str, status: StatusValue = StatusValue.VERIFIED, detail: str = "ok") -> EvidenceFact:
    return EvidenceFact(
        category="system_health", label=label, status=status, detail=detail, source="test",
        collected_at=0,
    )


def test_render_fleet_status_includes_status_legend_and_facts() -> None:
    doc = markdown_gen.render_fleet_status((_fact("disk"),), generated_at=0)
    assert "Fleet Status" in doc
    assert "Implemented" in doc  # from the legend
    assert "disk" in doc
    assert "Provenance" in doc


def test_render_fleet_status_empty_facts_says_so() -> None:
    doc = markdown_gen.render_fleet_status((), generated_at=0)
    assert "No evidence collected" in doc


def test_render_operations_dashboard_lists_contradictions() -> None:
    contradiction = Contradiction(category="c", label="x", description="conflicting statuses", sources=("a", "b"))
    doc = markdown_gen.render_operations_dashboard((_fact("disk"),), (contradiction,), generated_at=0)
    assert "conflicting statuses" in doc


def test_render_operations_dashboard_includes_vault_contradictions() -> None:
    vault_item = VaultContradiction(vault_path="00-Inbox/incidents/x.md", title="Old Incident")
    doc = markdown_gen.render_operations_dashboard(
        (), (), generated_at=0, vault_contradictions=(vault_item,)
    )
    assert "[[Old Incident]]" in doc


def test_render_verification_matrix_has_full_table() -> None:
    doc = markdown_gen.render_verification_matrix((_fact("disk"), _fact("memory")), generated_at=0)
    assert "disk" in doc
    assert "memory" in doc


def test_render_daily_evidence_includes_collector_errors() -> None:
    snapshot = EvidenceSnapshot(
        run_id="r1", collected_at=0, facts=(_fact("disk"),),
        collector_errors=("systemd_state collector failed: no systemctl",),
    )
    doc = markdown_gen.render_daily_evidence(snapshot, generated_at=0)
    assert "systemd_state collector failed" in doc


def test_render_daily_city_report_without_prose_notes_skip() -> None:
    snapshot = EvidenceSnapshot(run_id="r1", collected_at=0, facts=(_fact("disk"),))
    doc = markdown_gen.render_daily_city_report(
        snapshot, generated_at=0, prose=None, date_str="2026-08-06"
    )
    assert "Prose generation was skipped" in doc


def test_render_daily_city_report_with_prose_includes_it() -> None:
    snapshot = EvidenceSnapshot(run_id="r1", collected_at=0, facts=(_fact("disk"),))
    doc = markdown_gen.render_daily_city_report(
        snapshot, generated_at=0, prose="A conservative summary.", date_str="2026-08-06"
    )
    assert "A conservative summary." in doc


def test_render_incident_draft_is_marked_as_draft() -> None:
    contradiction = Contradiction(category="c", label="x", description="something failed", sources=("a",))
    doc = markdown_gen.render_incident_draft(contradiction, generated_at=0, run_id="r1")
    assert "Draft" in doc
    assert "requires human review" in doc


def test_generated_documents_never_contain_secrets_even_if_a_detail_leaks_one() -> None:
    # Belt-and-suspenders: even though EvidenceFact construction should
    # already reject a secret in `detail`, markdown_gen's own final
    # redaction pass must still catch anything that slips through.
    fact = _fact("endpoint", detail="reachable at 192.168.50.7")
    doc = markdown_gen.render_fleet_status((fact,), generated_at=0)
    assert "192.168.50.7" not in doc
    assert "192.168.x.x" in doc
