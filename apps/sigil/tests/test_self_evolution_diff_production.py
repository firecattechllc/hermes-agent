from __future__ import annotations

import pytest

from sigil.self_evolution import (
    EvolutionFrameworkConfig,
    SelfEvolutionValidationError,
    produce_evidence_diff,
)


def test_produces_real_unified_diff_output() -> None:
    diff = produce_evidence_diff(
        old_content="line one\nline two\n", new_content="line one\nline three\n"
    )

    assert "-line two" in diff
    assert "+line three" in diff
    assert "line one" not in diff.split("\n")[0]  # unchanged context isn't in the header


def test_identical_content_produces_empty_diff() -> None:
    assert produce_evidence_diff(old_content="same\n", new_content="same\n") == ""


def test_uses_supplied_labels_in_diff_header() -> None:
    diff = produce_evidence_diff(
        old_content="a\n", new_content="b\n", old_label="config.py (before)", new_label="config.py (after)"
    )

    assert "config.py (before)" in diff
    assert "config.py (after)" in diff


def test_rejects_non_string_input() -> None:
    with pytest.raises(SelfEvolutionValidationError, match="string"):
        produce_evidence_diff(old_content=None, new_content="x")  # type: ignore[arg-type]


def test_rejects_oversized_input() -> None:
    huge = "x" * 600_000
    with pytest.raises(SelfEvolutionValidationError, match="exceeds"):
        produce_evidence_diff(old_content=huge, new_content="small")


def test_never_touches_filesystem_or_returns_non_string(tmp_path, monkeypatch) -> None:
    def forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("produce_evidence_diff must never touch the filesystem")

    monkeypatch.setattr("builtins.open", forbidden_open)

    result = produce_evidence_diff(old_content="a\n", new_content="b\n")

    assert isinstance(result, str)


def test_framework_config_still_denies_modify_source_regardless_of_diff_use() -> None:
    config = EvolutionFrameworkConfig()

    produce_evidence_diff(old_content="a\n", new_content="b\n")

    assert config.can_modify_source is False
