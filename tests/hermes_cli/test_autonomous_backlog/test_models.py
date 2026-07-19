"""Governed Autonomous Backlog domain model tests."""

from __future__ import annotations

import pytest

from hermes_cli.autonomous_backlog import models as m


def test_current_schema_version_is_supported() -> None:
    assert m.CURRENT_SCHEMA_VERSION in m.SUPPORTED_SCHEMA_VERSIONS


def test_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValueError, match="schema version 999 not supported"):
        m._validate_schema(999)


def test_supported_schema_version_is_returned() -> None:
    assert m._validate_schema(m.CURRENT_SCHEMA_VERSION) == 1


def test_generated_backlog_item_id_has_expected_prefix() -> None:
    identifier = m.new_backlog_item_id()

    assert identifier.startswith("backlog_")
    assert len(identifier) > len("backlog_")


def test_generated_backlog_event_id_has_expected_prefix() -> None:
    identifier = m.new_backlog_event_id()

    assert identifier.startswith("bevt_")
    assert len(identifier) > len("bevt_")


def test_generated_identifiers_are_unique() -> None:
    first = m.new_backlog_item_id()
    second = m.new_backlog_item_id()

    assert first != second


def test_identifier_prefix_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="identifier prefix must not be empty"):
        m._new_identifier("   ")


def test_backlog_status_values_are_stable() -> None:
    assert m.BacklogStatus.CANDIDATE.value == "candidate"
    assert m.BacklogStatus.AWAITING_APPROVAL.value == "awaiting_approval"
    assert m.BacklogStatus.UNKNOWN.value == "unknown"


def test_terminal_and_exception_statuses_exist() -> None:
    expected = {
        m.BacklogStatus.COMPLETED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.CANCELLED,
        m.BacklogStatus.SUPERSEDED,
        m.BacklogStatus.UNKNOWN,
    }

    assert expected.issubset(set(m.BacklogStatus))


def test_priority_values_are_stable() -> None:
    assert [priority.value for priority in m.BacklogPriority] == [
        "critical",
        "high",
        "normal",
        "low",
    ]


def test_risk_values_are_stable() -> None:
    assert [risk.value for risk in m.BacklogRiskLevel] == [
        "low",
        "medium",
        "high",
        "critical",
    ]


def test_source_types_include_completed_foundations() -> None:
    assert m.BacklogSourceType.CONTEXT_RECORD.value == "context_record"
    assert (
        m.BacklogSourceType.MISSION_CONTROL_EVENT.value
        == "mission_control_event"
    )
    assert (
        m.BacklogSourceType.ENGINEERING_MEMORY.value
        == "engineering_memory"
    )


def test_schedule_modes_are_stable() -> None:
    assert set(m.ScheduleMode) == {
        m.ScheduleMode.MANUAL,
        m.ScheduleMode.IMMEDIATE,
        m.ScheduleMode.SCHEDULED,
    }


def test_retry_modes_are_stable() -> None:
    assert set(m.RetryMode) == {
        m.RetryMode.NEVER,
        m.RetryMode.MANUAL,
        m.RetryMode.BOUNDED,
    }


def test_backlog_source_normalises_references() -> None:
    source = m.BacklogSource(
        source_type=m.BacklogSourceType.TEST_RESULT,
        source_refs=(
            "pytest:step4",
            " pytest:step4 ",
            "",
            "logs/result.txt",
        ),
        captured_at=10,
        captured_by="tester",
    )

    assert source.source_refs == (
        "pytest:step4",
        "logs/result.txt",
    )


def test_backlog_source_is_immutable() -> None:
    source = m.BacklogSource(
        source_type=m.BacklogSourceType.HUMAN,
        captured_at=10,
    )

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        source.captured_by = "rewriter"


def test_backlog_source_rejects_negative_timestamp() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="captured_at must be a non-negative",
    ):
        m.BacklogSource(
            source_type=m.BacklogSourceType.HUMAN,
            captured_at=-1,
        )


def test_evidence_requirement_strips_text() -> None:
    requirement = m.EvidenceRequirement(
        requirement_id=" focused-tests ",
        description=" Focused tests must pass. ",
        evidence_type=" pytest ",
    )

    assert requirement.requirement_id == "focused-tests"
    assert requirement.description == "Focused tests must pass."
    assert requirement.evidence_type == "pytest"


def test_evidence_requirement_rejects_blank_text() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="text fields must not be blank",
    ):
        m.EvidenceRequirement(
            requirement_id="   ",
            description="Focused tests must pass.",
        )


def test_manual_schedule_policy_is_default() -> None:
    policy = m.SchedulePolicy()

    assert policy.mode == m.ScheduleMode.MANUAL
    assert policy.scheduled_at is None


def test_scheduled_policy_requires_timestamp() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="scheduled mode requires scheduled_at",
    ):
        m.SchedulePolicy(mode=m.ScheduleMode.SCHEDULED)


def test_scheduled_at_is_invalid_for_non_scheduled_mode() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="scheduled_at is only valid",
    ):
        m.SchedulePolicy(
            mode=m.ScheduleMode.IMMEDIATE,
            scheduled_at=100,
        )


def test_schedule_window_must_be_ordered() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="expires_at must not be earlier",
    ):
        m.SchedulePolicy(
            not_before=200,
            expires_at=100,
        )


def test_valid_scheduled_policy() -> None:
    policy = m.SchedulePolicy(
        mode=m.ScheduleMode.SCHEDULED,
        scheduled_at=100,
        not_before=90,
        expires_at=200,
    )

    assert policy.scheduled_at == 100


def test_retry_policy_defaults_to_never() -> None:
    policy = m.RetryPolicy()

    assert policy.mode == m.RetryMode.NEVER
    assert policy.max_attempts == 1
    assert policy.backoff_seconds == 0


def test_never_retry_rejects_multiple_attempts() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="never retry mode requires max_attempts=1",
    ):
        m.RetryPolicy(
            mode=m.RetryMode.NEVER,
            max_attempts=2,
        )


def test_manual_retry_rejects_backoff() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="manual retry mode requires backoff_seconds=0",
    ):
        m.RetryPolicy(
            mode=m.RetryMode.MANUAL,
            backoff_seconds=10,
        )


def test_bounded_retry_requires_multiple_attempts() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="bounded retry mode requires max_attempts",
    ):
        m.RetryPolicy(
            mode=m.RetryMode.BOUNDED,
            max_attempts=1,
        )


def test_valid_bounded_retry_policy() -> None:
    policy = m.RetryPolicy(
        mode=m.RetryMode.BOUNDED,
        max_attempts=3,
        backoff_seconds=60,
    )

    assert policy.max_attempts == 3
    assert policy.backoff_seconds == 60


def _source() -> m.BacklogSource:
    return m.BacklogSource(
        source_type=m.BacklogSourceType.HUMAN,
        source_refs=("roadmap:step4",),
        captured_at=10,
        captured_by="human",
    )


def _item(**overrides) -> m.BacklogItem:
    data = {
        "item_id": "backlog_1",
        "project_id": "hermes-platform",
        "title": "Build governed autonomous backlog",
        "description": "Implement the Step 4 backlog foundation.",
        "status": m.BacklogStatus.CANDIDATE,
        "priority": m.BacklogPriority.HIGH,
        "risk_level": m.BacklogRiskLevel.MEDIUM,
        "source": _source(),
        "dependencies": ["backlog_0", " backlog_0 ", ""],
        "blocked_by": [],
        "acceptance_criteria": [
            "Focused tests pass",
            " Focused tests pass ",
            "No regressions",
        ],
        "required_capabilities": ["python", " python "],
        "allowed_paths": ["hermes_cli/autonomous_backlog"],
        "denied_paths": ["hermes_cli/engineering_memory"],
        "created_at": 20,
        "updated_at": 20,
        "created_by": "human",
        "version": 1,
    }
    data.update(overrides)
    return m.BacklogItem(**data)


def test_candidate_backlog_item_defaults_and_normalisation() -> None:
    item = _item()

    assert item.status == m.BacklogStatus.CANDIDATE
    assert item.dependencies == ["backlog_0"]
    assert item.acceptance_criteria == [
        "Focused tests pass",
        "No regressions",
    ]
    assert item.required_capabilities == ["python"]


def test_backlog_item_strips_scalar_text() -> None:
    item = _item(
        title=" Build backlog ",
        description=" Manual implementation ",
        created_by=" human ",
    )

    assert item.title == "Build backlog"
    assert item.description == "Manual implementation"
    assert item.created_by == "human"


def test_backlog_item_rejects_blank_scalar_text() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="text fields must not be blank",
    ):
        _item(title="   ")


def test_unknown_backlog_schema_version_fails_closed() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="schema version 999 not supported",
    ):
        _item(schema_version=999)


def test_backlog_item_cannot_depend_on_itself() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="cannot depend on itself",
    ):
        _item(dependencies=["backlog_1"])


def test_backlog_item_cannot_be_blocked_by_itself() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="cannot be blocked by itself",
    ):
        _item(blocked_by=["backlog_1"])


def test_paths_cannot_be_allowed_and_denied() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="paths cannot be both allowed and denied",
    ):
        _item(
            allowed_paths=["hermes_cli"],
            denied_paths=["hermes_cli"],
        )


def test_superseded_item_requires_replacement() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="requires superseded_by",
    ):
        _item(status=m.BacklogStatus.SUPERSEDED)


def test_backlog_item_cannot_supersede_itself() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="cannot supersede itself",
    ):
        _item(
            status=m.BacklogStatus.SUPERSEDED,
            superseded_by="backlog_1",
        )


def test_failed_item_requires_reason() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="failed backlog item requires failure_reason",
    ):
        _item(status=m.BacklogStatus.FAILED)


def test_blocked_item_requires_reason() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="blocked backlog item requires blocked_reason",
    ):
        _item(status=m.BacklogStatus.BLOCKED)


def test_completion_requires_evidence_when_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="completed backlog item requires evidence_refs",
    ):
        _item(
            status=m.BacklogStatus.COMPLETED,
            evidence_requirements=[
                m.EvidenceRequirement(
                    requirement_id="focused-tests",
                    description="Focused tests pass.",
                )
            ],
            evidence_refs=[],
        )


def test_completion_accepts_supplied_evidence() -> None:
    item = _item(
        status=m.BacklogStatus.COMPLETED,
        evidence_requirements=[
            m.EvidenceRequirement(
                requirement_id="focused-tests",
                description="Focused tests pass.",
            )
        ],
        evidence_refs=["pytest:29-passed"],
    )

    assert item.status == m.BacklogStatus.COMPLETED


def test_unknown_item_cannot_use_bounded_retry() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="cannot permit automatic bounded retry",
    ):
        _item(
            status=m.BacklogStatus.UNKNOWN,
            retry_policy=m.RetryPolicy(
                mode=m.RetryMode.BOUNDED,
                max_attempts=3,
                backoff_seconds=60,
            ),
        )


def test_unknown_item_allows_manual_retry_policy() -> None:
    item = _item(
        status=m.BacklogStatus.UNKNOWN,
        retry_policy=m.RetryPolicy(
            mode=m.RetryMode.MANUAL,
        ),
    )

    assert item.status == m.BacklogStatus.UNKNOWN


def test_timestamp_before_creation_is_normalised() -> None:
    item = _item(created_at=20, updated_at=10)

    assert item.updated_at == 20


def test_content_fingerprint_is_stable_across_identity_and_time() -> None:
    first = _item(
        item_id="backlog_a",
        created_at=20,
        updated_at=20,
    )
    second = _item(
        item_id="backlog_b",
        created_at=99,
        updated_at=100,
    )

    assert first.content_fingerprint() == second.content_fingerprint()


def test_content_fingerprint_changes_with_semantic_content() -> None:
    first = _item()
    second = _item(description="Different engineering work.")

    assert first.content_fingerprint() != second.content_fingerprint()


def _event(**overrides) -> m.BacklogEvent:
    data = {
        "event_id": "bevt_1",
        "event_type": m.BacklogEventType.CREATED,
        "project_id": "hermes-platform",
        "item_id": "backlog_1",
        "timestamp": 10,
        "sequence": 1,
        "actor": "human",
        "expected_version": 0,
        "resulting_version": 1,
        "payload": {
            "item": _item().model_dump(mode="json"),
        },
    }
    data.update(overrides)
    return m.BacklogEvent(**data)


def test_backlog_event_is_immutable() -> None:
    from pydantic import ValidationError

    event = _event()

    with pytest.raises(ValidationError):
        event.actor = "rewriter"


def test_backlog_event_stable_sort_key() -> None:
    event = _event(
        event_id="bevt_2",
        timestamp=20,
        sequence=3,
    )

    assert event.stable_sort_key() == (
        20,
        3,
        "bevt_2",
    )


def test_backlog_event_rejects_negative_timestamp() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="timestamp must be a non-negative",
    ):
        _event(timestamp=-1)


def test_backlog_event_rejects_blank_actor() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="text fields must not be blank",
    ):
        _event(actor="   ")


def test_backlog_event_version_increment_must_match() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="resulting_version must equal expected_version",
    ):
        _event(
            expected_version=3,
            resulting_version=7,
        )


def test_backlog_event_allows_missing_expected_version() -> None:
    event = _event(
        expected_version=None,
        resulting_version=4,
    )

    assert event.resulting_version == 4


def test_backlog_event_integrity_hash_is_deterministic() -> None:
    first = _event()
    second = _event()

    assert first.integrity_hash() == second.integrity_hash()


def test_backlog_event_integrity_hash_changes_with_payload() -> None:
    first = _event(payload={"value": 1})
    second = _event(payload={"value": 2})

    assert first.integrity_hash() != second.integrity_hash()


def test_backlog_event_unknown_schema_fails_closed() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="schema version 999 not supported",
    ):
        _event(schema_version=999)


def test_snapshot_sorts_items_deterministically() -> None:
    snapshot = m.BacklogSnapshot(
        version=1,
        generated_at=100,
        project_id="hermes-platform",
        event_count=2,
        items=[
            _item(item_id="backlog_b"),
            _item(item_id="backlog_a"),
        ],
    )

    assert [
        item.item_id
        for item in snapshot.items
    ] == [
        "backlog_a",
        "backlog_b",
    ]


def test_snapshot_rejects_cross_project_items() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="another project",
    ):
        m.BacklogSnapshot(
            version=1,
            generated_at=100,
            project_id="hermes-platform",
            event_count=1,
            items=[
                _item(project_id="other-project"),
            ],
        )


def test_snapshot_rejects_duplicate_item_ids() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="duplicate backlog item ids",
    ):
        m.BacklogSnapshot(
            version=1,
            generated_at=100,
            project_id="hermes-platform",
            event_count=2,
            items=[
                _item(item_id="backlog_1"),
                _item(item_id="backlog_1"),
            ],
        )


def test_snapshot_integrity_hash_is_deterministic() -> None:
    first = m.BacklogSnapshot(
        version=1,
        generated_at=100,
        generated_by="first",
        project_id="hermes-platform",
        event_count=1,
        items=[_item()],
    )
    second = m.BacklogSnapshot(
        version=1,
        generated_at=200,
        generated_by="second",
        project_id="hermes-platform",
        event_count=1,
        items=[_item()],
    )

    assert first.integrity_hash() == second.integrity_hash()


def test_snapshot_integrity_hash_changes_with_state() -> None:
    first = m.BacklogSnapshot(
        version=1,
        generated_at=100,
        project_id="hermes-platform",
        event_count=1,
        items=[_item()],
    )
    second = m.BacklogSnapshot(
        version=1,
        generated_at=100,
        project_id="hermes-platform",
        event_count=2,
        items=[_item()],
    )

    assert first.integrity_hash() != second.integrity_hash()


def test_snapshot_unknown_schema_fails_closed() -> None:
    from pydantic import ValidationError

    with pytest.raises(
        ValidationError,
        match="schema version 999 not supported",
    ):
        m.BacklogSnapshot(
            version=1,
            generated_at=100,
            project_id="hermes-platform",
            event_count=0,
            schema_version=999,
        )
