from __future__ import annotations

from pathlib import Path

from hermes_docs_worker.evidence import EvidenceFact, EvidenceSnapshot
from hermes_docs_worker.significance import (
    ChangeSignificance,
    any_content_changed,
    generated_content_changed,
    score_evidence_change,
)
from hermes_docs_worker.status import StatusValue


def _snapshot(run_id: str, statuses: dict) -> EvidenceSnapshot:
    facts = tuple(
        EvidenceFact(category="c", label=label, status=status, source="s", collected_at=0)
        for label, status in statuses.items()
    )
    return EvidenceSnapshot(run_id=run_id, collected_at=0, facts=facts)


def test_score_first_run_with_facts_is_major() -> None:
    current = _snapshot("r1", {"x": StatusValue.VERIFIED})
    assert score_evidence_change(None, current) == ChangeSignificance.MAJOR


def test_score_first_run_with_no_facts_is_none() -> None:
    current = _snapshot("r1", {})
    assert score_evidence_change(None, current) == ChangeSignificance.NONE


def test_score_no_change() -> None:
    previous = _snapshot("r0", {"x": StatusValue.VERIFIED})
    current = _snapshot("r1", {"x": StatusValue.VERIFIED})
    assert score_evidence_change(previous, current) == ChangeSignificance.NONE


def test_score_severe_transition_is_major() -> None:
    previous = _snapshot("r0", {"x": StatusValue.VERIFIED})
    current = _snapshot("r1", {"x": StatusValue.BLOCKED})
    assert score_evidence_change(previous, current) == ChangeSignificance.MAJOR


def test_score_minor_change_for_a_single_calm_flip() -> None:
    previous = _snapshot("r0", {"x": StatusValue.CONFIGURED, "y": StatusValue.VERIFIED, "z": StatusValue.VERIFIED, "w": StatusValue.VERIFIED, "v": StatusValue.VERIFIED})
    current = _snapshot("r1", {"x": StatusValue.IMPLEMENTED, "y": StatusValue.VERIFIED, "z": StatusValue.VERIFIED, "w": StatusValue.VERIFIED, "v": StatusValue.VERIFIED})
    assert score_evidence_change(previous, current) == ChangeSignificance.MINOR


def test_generated_content_changed_true_when_file_missing(tmp_path: Path) -> None:
    result = generated_content_changed({Path("a.md"): "hello"}, tmp_path)
    assert result[Path("a.md")] is True


def test_generated_content_changed_false_when_identical(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    result = generated_content_changed({Path("a.md"): "hello"}, tmp_path)
    assert result[Path("a.md")] is False


def test_generated_content_changed_true_when_different(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("old", encoding="utf-8")
    result = generated_content_changed({Path("a.md"): "new"}, tmp_path)
    assert result[Path("a.md")] is True


def test_any_content_changed_no_change_is_the_idempotency_gate(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("same", encoding="utf-8")
    (tmp_path / "b.md").write_text("same", encoding="utf-8")
    assert any_content_changed({Path("a.md"): "same", Path("b.md"): "same"}, tmp_path) is False
    assert any_content_changed({Path("a.md"): "same", Path("b.md"): "different"}, tmp_path) is True
