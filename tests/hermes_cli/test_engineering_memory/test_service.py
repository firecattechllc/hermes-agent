"""Structured Engineering Memory service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.service import EngineeringMemoryService
from hermes_cli.engineering_memory.store import EngineeringMemoryStore


def _service(tmp_path: Path) -> EngineeringMemoryService:
    return EngineeringMemoryService(
        store=EngineeringMemoryStore(root=tmp_path)
    )


def _provenance(
    source_id: str = "manual:step-3.1",
) -> m.MemoryProvenance:
    return m.MemoryProvenance(
        source_type=m.MemorySourceType.HUMAN,
        source_ids=(source_id,),
        evidence_refs=("tests/output.txt",),
        captured_at=10,
        captured_by="maintainer",
    )


def _create_candidate(
    service: EngineeringMemoryService,
    *,
    project_id: str = "hermes-platform",
    memory_id: str = "mem_1",
    title: str = "Use locked journal writes",
    summary: str = "Sequence allocation and journal append share a lock.",
    source_id: str = "manual:step-3.1",
    source_key: str | None = None,
) -> m.MemoryRecord:
    return service.create_candidate(
        project_id,
        m.MemoryType.IMPLEMENTATION_LESSON,
        title,
        summary,
        provenance=_provenance(source_id),
        confidence=0.95,
        tags=["Persistence", "persistence", "locking"],
        created_by="maintainer",
        actor="maintainer",
        memory_id=memory_id,
        source_idempotency_key=source_key,
    )


def test_create_candidate_persists_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)

    memory = _create_candidate(service)

    assert memory.status == m.MemoryStatus.CANDIDATE
    assert memory.tags == ["persistence", "locking"]
    assert service.get_memory(
        "hermes-platform",
        "mem_1",
    ) == memory


def test_duplicate_content_returns_existing_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)

    first = _create_candidate(
        service,
        memory_id="mem_first",
    )
    second = _create_candidate(
        service,
        memory_id="mem_second",
    )

    assert second.memory_id == first.memory_id
    assert len(
        service.list_memories(
            "hermes-platform",
            include_inactive=True,
        )
    ) == 1


def test_source_idempotency_key_returns_existing_memory(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    first = _create_candidate(
        service,
        memory_id="mem_first",
        source_key="context:rec_1",
    )
    second = _create_candidate(
        service,
        memory_id="mem_second",
        title="Different title",
        summary="Different content from repeated delivery.",
        source_id="different-source",
        source_key="context:rec_1",
    )

    assert second.memory_id == first.memory_id


def test_verify_candidate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    verified = service.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
        review_note="Reproduced from evidence.",
        confidence=1.0,
    )

    assert verified.status == m.MemoryStatus.VERIFIED
    assert verified.reviewed_by == "reviewer"
    assert verified.reviewed_at is not None
    assert verified.review_note == "Reproduced from evidence."
    assert verified.confidence == 1.0


def test_verify_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    first = service.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
    )
    second = service.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="other-reviewer",
    )

    assert second == first
    assert service.build_snapshot("hermes-platform").event_count == 2


def test_cannot_verify_rejected_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    service.reject_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
        review_note="Could not reproduce.",
    )

    with pytest.raises(ValueError, match="cannot verify memory"):
        service.verify_memory(
            "hermes-platform",
            candidate.memory_id,
            reviewed_by="reviewer",
        )


def test_reject_candidate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    rejected = service.reject_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
        review_note="Evidence contradicted the claim.",
    )

    assert rejected.status == m.MemoryStatus.REJECTED
    assert rejected.reviewed_by == "reviewer"
    assert rejected.reviewed_at is not None
    assert rejected.review_note == "Evidence contradicted the claim."


def test_reject_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    first = service.reject_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
        review_note="Rejected.",
    )
    second = service.reject_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="another",
        review_note="Different note.",
    )

    assert second == first
    assert service.build_snapshot("hermes-platform").event_count == 2


def test_cannot_reject_verified_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    service.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
    )

    with pytest.raises(ValueError, match="cannot reject memory"):
        service.reject_memory(
            "hermes-platform",
            candidate.memory_id,
            reviewed_by="reviewer",
            review_note="Too late.",
        )


def test_supersede_candidate_creates_replacement(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = _create_candidate(service)

    replacement = service.supersede_memory(
        "hermes-platform",
        original.memory_id,
        replacement_type=m.MemoryType.IMPLEMENTATION_LESSON,
        replacement_title="Use one lock for sequence and append",
        replacement_summary=(
            "Sequence allocation and both journal writes must share one lock."
        ),
        replacement_provenance=_provenance("manual:correction"),
        replacement_memory_id="mem_2",
        actor="maintainer",
    )

    old = service.get_memory("hermes-platform", original.memory_id)

    assert replacement.memory_id == "mem_2"
    assert replacement.status == m.MemoryStatus.CANDIDATE
    assert replacement.supersedes == [original.memory_id]

    assert old is not None
    assert old.status == m.MemoryStatus.SUPERSEDED
    assert old.superseded_by == replacement.memory_id


def test_supersede_verified_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = _create_candidate(service)

    service.verify_memory(
        "hermes-platform",
        original.memory_id,
        reviewed_by="reviewer",
    )

    replacement = service.supersede_memory(
        "hermes-platform",
        original.memory_id,
        replacement_type=m.MemoryType.IMPLEMENTATION_LESSON,
        replacement_title="Corrected locked-write rule",
        replacement_summary="Corrected and expanded engineering rule.",
        replacement_provenance=_provenance("manual:verified-correction"),
        replacement_memory_id="mem_replacement",
        actor="maintainer",
    )

    assert replacement.status == m.MemoryStatus.CANDIDATE
    assert service.build_snapshot("hermes-platform").event_count == 4


def test_supersede_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = _create_candidate(service)

    first = service.supersede_memory(
        "hermes-platform",
        original.memory_id,
        replacement_type=m.MemoryType.IMPLEMENTATION_LESSON,
        replacement_title="Replacement",
        replacement_summary="Replacement summary.",
        replacement_provenance=_provenance("manual:replacement"),
        replacement_memory_id="mem_2",
    )

    second = service.supersede_memory(
        "hermes-platform",
        original.memory_id,
        replacement_type=m.MemoryType.IMPLEMENTATION_LESSON,
        replacement_title="Ignored second replacement",
        replacement_summary="Ignored.",
        replacement_provenance=_provenance("manual:ignored"),
        replacement_memory_id="mem_3",
    )

    assert second == first
    assert service.get_memory("hermes-platform", "mem_3") is None


def test_cannot_supersede_rejected_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = _create_candidate(service)

    service.reject_memory(
        "hermes-platform",
        original.memory_id,
        reviewed_by="reviewer",
        review_note="Rejected.",
    )

    with pytest.raises(ValueError, match="cannot supersede memory"):
        service.supersede_memory(
            "hermes-platform",
            original.memory_id,
            replacement_type=m.MemoryType.IMPLEMENTATION_LESSON,
            replacement_title="Replacement",
            replacement_summary="Replacement summary.",
            replacement_provenance=_provenance("manual:replacement"),
        )


def test_archive_candidate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    archived = service.archive_memory(
        "hermes-platform",
        candidate.memory_id,
        actor="maintainer",
        archive_note="No longer relevant.",
    )

    assert archived.status == m.MemoryStatus.ARCHIVED
    assert archived.review_note == "No longer relevant."


def test_archive_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    first = service.archive_memory(
        "hermes-platform",
        candidate.memory_id,
    )
    second = service.archive_memory(
        "hermes-platform",
        candidate.memory_id,
    )

    assert second == first
    assert service.build_snapshot("hermes-platform").event_count == 2


def test_cannot_archive_superseded_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = _create_candidate(service)

    service.supersede_memory(
        "hermes-platform",
        original.memory_id,
        replacement_type=m.MemoryType.IMPLEMENTATION_LESSON,
        replacement_title="Replacement",
        replacement_summary="Replacement summary.",
        replacement_provenance=_provenance("manual:replacement"),
        replacement_memory_id="mem_2",
    )

    with pytest.raises(ValueError, match="cannot archive superseded memory"):
        service.archive_memory(
            "hermes-platform",
            original.memory_id,
        )


def test_missing_memory_fails_clearly(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="no such memory"):
        service.verify_memory(
            "hermes-platform",
            "mem_missing",
            reviewed_by="reviewer",
        )


def test_project_isolation(tmp_path: Path) -> None:
    service = _service(tmp_path)

    memory_a = _create_candidate(
        service,
        project_id="project-a",
        memory_id="mem_a",
    )
    memory_b = _create_candidate(
        service,
        project_id="project-b",
        memory_id="mem_b",
    )

    assert service.get_memory("project-a", memory_a.memory_id) is not None
    assert service.get_memory("project-a", memory_b.memory_id) is None

    with pytest.raises(ValueError, match="no such memory"):
        service.verify_memory(
            "project-a",
            memory_b.memory_id,
            reviewed_by="reviewer",
        )


def test_default_listing_hides_inactive_memories(tmp_path: Path) -> None:
    service = _service(tmp_path)

    candidate = _create_candidate(
        service,
        memory_id="mem_candidate",
    )
    rejected = _create_candidate(
        service,
        memory_id="mem_rejected",
        title="Rejected claim",
        summary="This claim will be rejected.",
        source_id="manual:rejected",
    )

    service.reject_memory(
        "hermes-platform",
        rejected.memory_id,
        reviewed_by="reviewer",
        review_note="Not reproducible.",
    )

    visible = service.list_memories("hermes-platform")
    all_memories = service.list_memories(
        "hermes-platform",
        include_inactive=True,
    )

    assert [memory.memory_id for memory in visible] == [
        candidate.memory_id
    ]
    assert {memory.memory_id for memory in all_memories} == {
        candidate.memory_id,
        rejected.memory_id,
    }


def test_snapshot_contains_latest_governed_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _create_candidate(service)

    service.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
    )

    snapshot = service.build_snapshot(
        "hermes-platform",
        generated_by="test",
    )

    assert snapshot.version == 2
    assert snapshot.event_count == 2
    assert snapshot.memories[0].status == m.MemoryStatus.VERIFIED
