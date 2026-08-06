from __future__ import annotations

from hermes_docs_worker.collectors import git_state
from hermes_docs_worker.status import StatusValue


def test_reports_verified_repository_state(worker_config) -> None:
    facts = {f.label: f for f in git_state.collect(worker_config, now=0)}
    assert facts["repository"].status == StatusValue.VERIFIED
    assert "HEAD=" in facts["repository"].detail
    assert "recent_commits" in facts


def test_missing_source_dir_is_unknown(worker_config, tmp_path) -> None:
    object.__setattr__(worker_config, "hermes_source_dir", tmp_path / "does-not-exist")
    facts = {f.label: f for f in git_state.collect(worker_config, now=0)}
    assert facts["repository"].status == StatusValue.UNKNOWN


def test_non_git_directory_is_unknown(worker_config, tmp_path) -> None:
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    object.__setattr__(worker_config, "hermes_source_dir", plain_dir)
    facts = {f.label: f for f in git_state.collect(worker_config, now=0)}
    assert facts["repository"].status == StatusValue.UNKNOWN
