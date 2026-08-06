from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_docs_worker.repo_sync import RepoGuardError, ensure_repo_synced


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_ensure_repo_synced_succeeds_on_clean_up_to_date_repo(worker_config) -> None:
    status = ensure_repo_synced(worker_config)
    assert status.branch == "main"


def test_fails_closed_on_missing_path(worker_config, tmp_path: Path) -> None:
    object.__setattr__(worker_config, "docs_repo_path", tmp_path / "does-not-exist")
    with pytest.raises(RepoGuardError, match="does not exist"):
        ensure_repo_synced(worker_config)


def test_fails_closed_on_dirty_checkout(worker_config) -> None:
    (worker_config.docs_repo_path / "untracked.md").write_text("dirty", encoding="utf-8")
    with pytest.raises(RepoGuardError, match="uncommitted"):
        ensure_repo_synced(worker_config)


def test_fails_closed_when_not_on_main_branch(worker_config) -> None:
    _git(worker_config.docs_repo_path, "checkout", "-q", "-b", "some-other-branch")
    with pytest.raises(RepoGuardError, match="expected 'main'"):
        ensure_repo_synced(worker_config)


def test_fails_closed_on_unreachable_remote(worker_config) -> None:
    _git(worker_config.docs_repo_path, "remote", "set-url", "origin", "/nonexistent/remote.git")
    with pytest.raises(RepoGuardError, match="unreachable"):
        ensure_repo_synced(worker_config)


def test_fast_forwards_when_behind_but_not_diverged(worker_config, tmp_path: Path) -> None:
    # Simulate another clone pushing a new commit to origin/main.
    other_clone = tmp_path / "other-clone"
    subprocess.run(
        ["git", "clone", "-q", str((worker_config.docs_repo_path).parent / "origin.git"), str(other_clone)],
        check=True, capture_output=True, text=True,
    )
    _git(other_clone, "checkout", "-q", "main")
    (other_clone / "new.md").write_text("new content", encoding="utf-8")
    _git(other_clone, "add", "new.md")
    _git(other_clone, "-c", "user.email=a@b.c", "-c", "user.name=Other", "commit", "-q", "-m", "add new.md")
    _git(other_clone, "push", "-q", "origin", "main")

    status = ensure_repo_synced(worker_config)
    assert (worker_config.docs_repo_path / "new.md").exists()
    assert status.head_sha


def test_fails_closed_on_true_divergence(worker_config, tmp_path: Path) -> None:
    other_clone = tmp_path / "other-clone-2"
    subprocess.run(
        ["git", "clone", "-q", str((worker_config.docs_repo_path).parent / "origin.git"), str(other_clone)],
        check=True, capture_output=True, text=True,
    )
    _git(other_clone, "checkout", "-q", "main")
    (other_clone / "remote-only.md").write_text("remote", encoding="utf-8")
    _git(other_clone, "add", "remote-only.md")
    _git(other_clone, "-c", "user.email=a@b.c", "-c", "user.name=Other", "commit", "-q", "-m", "remote commit")
    _git(other_clone, "push", "-q", "origin", "main")

    (worker_config.docs_repo_path / "local-only.md").write_text("local", encoding="utf-8")
    _git(worker_config.docs_repo_path, "add", "local-only.md")
    _git(
        worker_config.docs_repo_path, "-c", "user.email=a@b.c", "-c", "user.name=Local",
        "commit", "-q", "-m", "local commit",
    )

    with pytest.raises(RepoGuardError, match="diverged"):
        ensure_repo_synced(worker_config)
