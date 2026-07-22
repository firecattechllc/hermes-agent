from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.context_engine import models as m
from hermes_cli.context_engine.renderer import render_context
from hermes_cli.context_engine.service import ContextService
from hermes_cli.context_engine.store import ContextStore, _load_json_safe


def test_missing_json_returns_none(tmp_path: Path) -> None:
    assert _load_json_safe(tmp_path / "missing.json") is None


def test_malformed_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSON"):
        _load_json_safe(path)


def test_project_registration_persists_and_reloads(tmp_path: Path) -> None:
    store = ContextStore(root=tmp_path / "context")
    service = ContextService(store=store)

    created = service.register_project(
        display_name="Hermes Platform",
        project_id="hermes-platform",
        repository_identity="https://github.com/NousResearch/hermes-agent.git",
        local_path="/tmp/hermes-platform",
        default_branch="dev",
        actor="test",
    )

    reloaded = service.get_project("hermes-platform")

    assert reloaded is not None
    assert reloaded == created
    assert reloaded.display_name == "Hermes Platform"
    assert (
        tmp_path
        / "context"
        / "projects"
        / "hermes-platform"
        / "project.json"
    ).is_file()


def test_project_registration_is_idempotent(tmp_path: Path) -> None:
    service = ContextService(store=ContextStore(root=tmp_path / "context"))

    first = service.register_project(
        display_name="Hermes Platform",
        project_id="hermes-platform",
        repository_identity="repo-a",
    )
    second = service.register_project(
        display_name="Ignored Rename",
        project_id="hermes-platform",
        repository_identity="repo-a",
    )

    assert second == first


def test_project_identity_conflict_is_rejected(tmp_path: Path) -> None:
    service = ContextService(store=ContextStore(root=tmp_path / "context"))

    service.register_project(
        display_name="Hermes Platform",
        project_id="hermes-platform",
        repository_identity="repo-a",
    )

    with pytest.raises(ValueError, match="already registered"):
        service.register_project(
            display_name="Hermes Platform",
            project_id="hermes-platform",
            repository_identity="repo-b",
        )


def test_snapshot_renderer_uses_display_name_and_stable_hash(tmp_path: Path) -> None:
    service = ContextService(store=ContextStore(root=tmp_path / "context"))

    service.register_project(
        display_name="Hermes Platform",
        project_id="hermes-platform",
        repository_identity="repo-a",
    )
    service.add_record(
        project_id="hermes-platform",
        record_type=m.RecordType.OBJECTIVE,
        title="Certify context",
        body="Validate persistence and rendering.",
        confidence=1.0,
    )

    snapshot = service.build_snapshot("hermes-platform")
    package = render_context(snapshot, role="all")

    assert package.project_name == "Hermes Platform"
    assert len(package.active_objectives) == 1
    assert len(snapshot.integrity_hash()) == 32
    assert snapshot.integrity_hash() == snapshot.integrity_hash()


def test_snapshot_hash_is_key_order_independent() -> None:
    first = m.ContextSnapshot(
        version=0,
        generated_at=1,
        project_id="hermes-platform",
        event_count=0,
    )
    second_payload = json.loads(first.model_dump_json())
    second = m.ContextSnapshot(**dict(reversed(list(second_payload.items()))))

    assert first.integrity_hash() == second.integrity_hash()


def test_snapshot_hash_ignores_generation_metadata() -> None:
    first = m.ContextSnapshot(
        version=0,
        generated_at=1,
        generated_by="cli",
        project_id="hermes-platform",
        event_count=0,
    )
    second = m.ContextSnapshot(
        **{
            **first.model_dump(),
            "generated_at": 2,
            "generated_by": "test",
        }
    )

    assert first.integrity_hash() == second.integrity_hash()


def test_build_snapshot_rejects_unknown_project(tmp_path: Path) -> None:
    service = ContextService(store=ContextStore(root=tmp_path / "context"))

    with pytest.raises(ValueError, match="no such project: missing-project"):
        service.build_snapshot("missing-project")


def test_event_log_honors_explicit_store_root(tmp_path: Path) -> None:
    root = tmp_path / "context"
    service = ContextService(store=ContextStore(root=root))

    service.register_project(
        display_name="Hermes Platform",
        project_id="hermes-platform",
        repository_identity="repo-a",
    )
    service.add_record(
        project_id="hermes-platform",
        record_type=m.RecordType.OBJECTIVE,
        title="Protect audit isolation",
        body="Ensure events remain inside the configured context root.",
        confidence=1.0,
    )

    event_path = root / "events.jsonl"

    assert event_path.exists()
    assert event_path.read_text(encoding="utf-8").strip()


def test_list_projects_returns_empty_for_fresh_store(tmp_path: Path) -> None:
    store = ContextStore(root=tmp_path / "context")

    assert store.list_projects() == []
