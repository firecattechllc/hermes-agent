from pathlib import Path

from hermes_cli.autonomous_backlog import (
    AutonomousBacklogService,
    AutonomousBacklogStore,
    BacklogSource,
    BacklogSourceType,
    BacklogStatus,
)


def test_public_package_surface_supports_end_to_end_flow(
    tmp_path: Path,
) -> None:
    store = AutonomousBacklogStore(
        tmp_path / "autonomous-backlog"
    )
    service = AutonomousBacklogService(store)

    created = service.create_item(
        project_id="hermes-platform",
        item_id="backlog_1",
        title="Build autonomous backlog integration",
        description="Prove package exports and lifecycle wiring.",
        source=BacklogSource(
            source_type=BacklogSourceType.HUMAN,
            source_refs=("integration-test",),
            captured_at=10,
            captured_by="pytest",
        ),
        created_at=10,
    )

    approved = service.approve_item(
        "hermes-platform",
        "backlog_1",
        updated_at=20,
    )

    started = service.start_item(
        "hermes-platform",
        "backlog_1",
        updated_at=30,
    )

    completed = service.complete_item(
        "hermes-platform",
        "backlog_1",
        evidence_refs=["pytest:integration"],
        updated_at=40,
    )

    assert created.status is BacklogStatus.CANDIDATE
    assert approved.status is BacklogStatus.APPROVED
    assert started.status is BacklogStatus.EXECUTING
    assert completed.status is BacklogStatus.COMPLETED

    replayed = store.get_item(
        "hermes-platform",
        "backlog_1",
    )

    assert replayed == completed
    assert store.event_count(
        project_id="hermes-platform"
    ) == 4
