from __future__ import annotations

import pytest

from hermes_docs_worker.contradiction import Contradiction, detect_status_contradictions
from hermes_docs_worker.evidence import EvidenceFact
from hermes_docs_worker.status import StatusValue


def _fact(label: str, status: StatusValue, source: str) -> EvidenceFact:
    return EvidenceFact(category="c", label=label, status=status, source=source, collected_at=0)


def test_no_contradiction_when_statuses_agree() -> None:
    facts = (_fact("x", StatusValue.VERIFIED, "a"), _fact("x", StatusValue.VERIFIED, "b"))
    assert detect_status_contradictions(facts) == ()


def test_detects_conflicting_statuses_for_same_subject() -> None:
    facts = (_fact("x", StatusValue.DEPLOYED, "a"), _fact("x", StatusValue.BLOCKED, "b"))
    contradictions = detect_status_contradictions(facts)
    assert len(contradictions) == 1
    assert contradictions[0].category == "c"
    assert contradictions[0].label == "x"
    assert set(contradictions[0].sources) == {"a", "b"}


def test_different_subjects_never_contradict() -> None:
    facts = (_fact("x", StatusValue.DEPLOYED, "a"), _fact("y", StatusValue.BLOCKED, "b"))
    assert detect_status_contradictions(facts) == ()


def test_contradiction_model_rejects_secret_in_description() -> None:
    with pytest.raises(ValueError):
        Contradiction(category="c", label="x", description="token=abcdef123456", sources=())
