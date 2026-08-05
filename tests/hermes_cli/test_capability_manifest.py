from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hermes_cli.capability_manifest import (
    CapabilityCategory,
    CapabilityEntry,
    CapabilityManifest,
    CapabilityManifestError,
    CapabilityNode,
    CapabilityState,
    DEFAULT_MANIFEST_PATH,
    load_capability_manifest,
    validate_capability_manifest,
)


def _entry(**overrides) -> CapabilityEntry:
    fields = dict(
        key="test_capability",
        display_name="Test Capability",
        category=CapabilityCategory.CORE_TOOL,
        state=CapabilityState.ACTIVE,
        reason="a real reason",
        validation_command="python -c 'pass'",
        implementation_paths=("tools/example.py",),
    )
    fields.update(overrides)
    return CapabilityEntry(**fields)


# ── Real repository manifest ─────────────────────────────────────────────────

def test_default_manifest_file_exists() -> None:
    assert DEFAULT_MANIFEST_PATH.exists()


def test_default_manifest_loads_and_validates() -> None:
    manifest = load_capability_manifest()
    assert manifest.schema_version == 1
    assert len(manifest.entries) > 0
    ok, warnings = validate_capability_manifest(manifest)
    assert ok, warnings


def test_default_manifest_has_no_duplicate_keys() -> None:
    manifest = load_capability_manifest()
    keys = [e.key for e in manifest.entries]
    assert len(keys) == len(set(keys))


def test_default_manifest_covers_every_state() -> None:
    manifest = load_capability_manifest()
    counts = manifest.counts_by_state()
    # Every state except failed_validation should have at least one entry
    # for this deployment (failed_validation is legitimately empty when
    # nothing has regressed).
    for state in CapabilityState:
        if state == CapabilityState.FAILED_VALIDATION:
            continue
        assert counts[state.value] > 0, f"no entries recorded for state {state.value}"


def test_default_manifest_blocked_credentials_entries_declare_credentials() -> None:
    manifest = load_capability_manifest()
    for entry in manifest.by_state(CapabilityState.BLOCKED_CREDENTIALS):
        assert entry.requires_credentials, f"{entry.key} is blocked_credentials but declares none"


def test_default_manifest_active_entries_have_validation_commands() -> None:
    manifest = load_capability_manifest()
    for entry in manifest.by_state(CapabilityState.ACTIVE):
        assert entry.validation_command, f"{entry.key} is active but has no validation_command"


def test_hermes_link_titan_and_mac_entries_present() -> None:
    manifest = load_capability_manifest()
    assert manifest.find("hermes_link_titan_node") is not None
    assert manifest.find("hermes_link_mac_node") is not None
    assert manifest.find("hermes_link_titan_node").node == CapabilityNode.TITAN
    assert manifest.find("hermes_link_mac_node").node == CapabilityNode.MAC


def test_by_node_filters_correctly() -> None:
    manifest = load_capability_manifest()
    titan_entries = manifest.by_node(CapabilityNode.TITAN)
    assert all(e.node == CapabilityNode.TITAN for e in titan_entries)
    assert len(titan_entries) > 0


# ── Schema / loader behavior (synthetic fixtures) ───────────────────────────

def test_capability_entry_rejects_uppercase_key() -> None:
    with pytest.raises(ValidationError):
        _entry(key="NotSnakeCase")


def test_manifest_rejects_duplicate_keys() -> None:
    with pytest.raises(ValidationError):
        CapabilityManifest(
            generated_at="2026-01-01",
            entries=(_entry(key="dup"), _entry(key="dup")),
        )


def test_manifest_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValidationError):
        CapabilityManifest(
            schema_version=99,
            generated_at="2026-01-01",
            entries=(_entry(),),
        )


def test_load_capability_manifest_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CapabilityManifestError):
        load_capability_manifest(tmp_path / "does-not-exist.yaml")


def test_load_capability_manifest_rejects_malformed_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid: yaml: at all", encoding="utf-8")
    with pytest.raises(CapabilityManifestError):
        load_capability_manifest(bad)


def test_load_capability_manifest_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(CapabilityManifestError):
        load_capability_manifest(bad)


def test_load_capability_manifest_rejects_schema_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad-schema.yaml"
    bad.write_text(
        yaml.safe_dump({"generated_at": "2026-01-01", "entries": [{"key": "x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(CapabilityManifestError):
        load_capability_manifest(bad)


def test_load_capability_manifest_accepts_well_formed_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "good.yaml"
    fixture.write_text(
        yaml.safe_dump(
            {
                "generated_at": "2026-01-01",
                "entries": [
                    {
                        "key": "example",
                        "display_name": "Example",
                        "category": "core_tool",
                        "state": "active",
                        "reason": "works",
                        "validation_command": "true",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_capability_manifest(fixture)
    assert manifest.find("example").state == CapabilityState.ACTIVE


# ── Semantic validation warnings ────────────────────────────────────────────

def test_validate_flags_active_without_validation_command() -> None:
    manifest = CapabilityManifest(
        generated_at="2026-01-01",
        entries=(_entry(state=CapabilityState.ACTIVE, validation_command=None),),
    )
    ok, warnings = validate_capability_manifest(manifest)
    assert ok is False
    assert any("no validation_command" in w for w in warnings)


def test_validate_flags_blocked_credentials_without_credentials() -> None:
    manifest = CapabilityManifest(
        generated_at="2026-01-01",
        entries=(
            _entry(
                state=CapabilityState.BLOCKED_CREDENTIALS,
                requires_credentials=(),
            ),
        ),
    )
    ok, warnings = validate_capability_manifest(manifest)
    assert ok is False
    assert any("requires_credentials is empty" in w for w in warnings)


def test_validate_flags_missing_implementation_paths() -> None:
    manifest = CapabilityManifest(
        generated_at="2026-01-01",
        entries=(_entry(implementation_paths=()),),
    )
    ok, warnings = validate_capability_manifest(manifest)
    assert ok is False
    assert any("no implementation_paths" in w for w in warnings)


def test_validate_passes_a_clean_manifest() -> None:
    manifest = CapabilityManifest(generated_at="2026-01-01", entries=(_entry(),))
    ok, warnings = validate_capability_manifest(manifest)
    assert ok is True
    assert warnings == ()
