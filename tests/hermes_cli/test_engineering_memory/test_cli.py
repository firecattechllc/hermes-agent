"""Engineering Memory CLI parser and runtime tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import engineering_memory_commands as commands
from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.service import (
    EngineeringMemoryService,
)
from hermes_cli.engineering_memory.store import (
    EngineeringMemoryStore,
)


@pytest.fixture
def service(tmp_path: Path) -> EngineeringMemoryService:
    return EngineeringMemoryService(
        store=EngineeringMemoryStore(root=tmp_path)
    )


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command")
    commands.build_engineering_memory_parser(sub)
    return root


def _run(
    parser: argparse.ArgumentParser,
    argv: list[str],
) -> int:
    args = parser.parse_args(argv)
    return commands.engineering_memory_command(args)


def test_parser_exposes_all_governed_actions(
    parser: argparse.ArgumentParser,
) -> None:
    for action in [
        "create",
        "list",
        "show",
        "verify",
        "reject",
        "supersede",
        "archive",
        "snapshot",
        "events",
    ]:
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(
                ["engineering-memory", action, "--help"]
            )
        assert exc_info.value.code == 0


def test_existing_memory_command_is_not_redefined() -> None:
    source = Path(
        "hermes_cli/subcommands/engineering_memory.py"
    ).read_text(encoding="utf-8")

    assert 'add_parser("memory"' not in source
    assert "engineering-memory" in source


def test_create_produces_candidate_only(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "create",
            "hermes-platform",
            "implementation_lesson",
            "Use locked journal writes",
            "Sequence allocation and append share one lock.",
            "--source-id",
            "manual:test",
            "--evidence",
            "tests/store.log",
            "--tag",
            "persistence",
        ],
    )

    assert result == 0
    memories = service.list_memories("hermes-platform")
    assert len(memories) == 1
    assert memories[0].status == m.MemoryStatus.CANDIDATE
    assert memories[0].reviewed_by is None
    assert "created candidate" in capsys.readouterr().out


def test_create_json_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "create",
            "hermes-platform",
            "invariant",
            "Candidate-only ingestion",
            "Adapters never verify imported knowledge.",
            "--json",
        ],
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "candidate"
    assert payload["memory_type"] == "invariant"


def test_verify_requires_explicit_reviewer(
    parser: argparse.ArgumentParser,
) -> None:
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "engineering-memory",
                "verify",
                "hermes-platform",
                "mem_1",
            ]
        )


def test_verify_transitions_candidate(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
) -> None:
    candidate = service.create_candidate(
        "hermes-platform",
        m.MemoryType.TEST_EVIDENCE,
        "Focused suite passed",
        "Engineering Memory focused tests passed.",
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.TEST_RESULT,
            source_ids=("pytest",),
        ),
    )

    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "verify",
            "hermes-platform",
            candidate.memory_id,
            "--reviewer",
            "independent-reviewer",
            "--note",
            "Evidence inspected.",
            "--confidence",
            "1.0",
        ],
    )

    assert result == 0
    verified = service.get_memory(
        "hermes-platform",
        candidate.memory_id,
    )
    assert verified is not None
    assert verified.status == m.MemoryStatus.VERIFIED
    assert verified.reviewed_by == "independent-reviewer"


def test_reject_requires_reason(
    parser: argparse.ArgumentParser,
) -> None:
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "engineering-memory",
                "reject",
                "hermes-platform",
                "mem_1",
                "--reviewer",
                "reviewer",
            ]
        )


def test_list_filters_status(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = service.create_candidate(
        "hermes-platform",
        m.MemoryType.IMPLEMENTATION_LESSON,
        "Candidate lesson",
        "Still awaiting review.",
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.HUMAN,
        ),
    )
    second = service.create_candidate(
        "hermes-platform",
        m.MemoryType.INVARIANT,
        "Verified invariant",
        "Must always hold.",
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.HUMAN,
            source_ids=("second",),
        ),
    )
    service.verify_memory(
        "hermes-platform",
        second.memory_id,
        reviewed_by="reviewer",
    )

    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "list",
            "hermes-platform",
            "--status",
            "verified",
            "--json",
        ],
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert [
        item["memory_id"]
        for item in payload["memories"]
    ] == [second.memory_id]
    assert first.memory_id not in {
        item["memory_id"]
        for item in payload["memories"]
    }


def test_supersede_replacement_stays_candidate(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
) -> None:
    old = service.create_candidate(
        "hermes-platform",
        m.MemoryType.ARCHITECTURE_DECISION,
        "Old decision",
        "Use the first design.",
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.HUMAN,
        ),
    )
    service.verify_memory(
        "hermes-platform",
        old.memory_id,
        reviewed_by="reviewer",
    )

    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "supersede",
            "hermes-platform",
            old.memory_id,
            "architecture_decision",
            "Replacement decision",
            "Use the improved design.",
            "--actor",
            "architect",
        ],
    )

    assert result == 0

    old_after = service.get_memory(
        "hermes-platform",
        old.memory_id,
    )
    assert old_after is not None
    assert old_after.status == m.MemoryStatus.SUPERSEDED

    replacement = service.get_memory(
        "hermes-platform",
        old_after.superseded_by,
    )
    assert replacement is not None
    assert replacement.status == m.MemoryStatus.CANDIDATE


def test_snapshot_json_contains_integrity_hash(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service.create_candidate(
        "hermes-platform",
        m.MemoryType.PROJECT_CONVENTION,
        "Use project isolation",
        "Every read and write is scoped by project ID.",
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.HUMAN,
        ),
    )

    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "snapshot",
            "hermes-platform",
            "--json",
        ],
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"] == "hermes-platform"
    assert payload["event_count"] == 1
    assert len(payload["integrity_hash"]) == 32


def test_events_use_public_service_method(
    monkeypatch: pytest.MonkeyPatch,
    parser: argparse.ArgumentParser,
    service: EngineeringMemoryService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = service.create_candidate(
        "hermes-platform",
        m.MemoryType.DEBUGGING_LESSON,
        "Failure reproduced",
        "The defect was reproduced deterministically.",
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.TEST_RESULT,
        ),
    )
    service.verify_memory(
        "hermes-platform",
        memory.memory_id,
        reviewed_by="reviewer",
    )

    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    result = _run(
        parser,
        [
            "engineering-memory",
            "events",
            "hermes-platform",
            "--json",
        ],
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["events"]) == 2
    assert payload["events"][0]["event_type"] == (
        "memory_created"
    )
    assert payload["events"][1]["event_type"] == (
        "memory_verified"
    )


def test_alias_parses(
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(
        [
            "eng-memory",
            "list",
            "hermes-platform",
        ]
    )
    assert args.engineering_memory_action == "list"
