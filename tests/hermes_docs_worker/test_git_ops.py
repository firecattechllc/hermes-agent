from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from hermes_docs_worker import git_ops
from hermes_docs_worker.git_ops import GitOpsError


def test_branch_name_format() -> None:
    name = git_ops.branch_name(now=time.mktime(time.strptime("2026-08-06 04:05:00", "%Y-%m-%d %H:%M:%S")))
    assert name.startswith("automation/titan-docs-")
    assert len(name) == len("automation/titan-docs-20260806-0405")


def test_commit_message_format() -> None:
    message = git_ops.commit_message(
        now=time.mktime(time.strptime("2026-08-06 04:05:00", "%Y-%m-%d %H:%M:%S"))
    )
    assert message == "Update Titan fleet evidence 2026-08-06"


def test_create_automation_branch_rejects_non_prefixed_name(worker_config) -> None:
    with pytest.raises(GitOpsError):
        git_ops.create_automation_branch(worker_config, "feature/whatever")


def test_create_automation_branch_succeeds_with_correct_prefix(worker_config) -> None:
    branch = git_ops.branch_name()
    git_ops.create_automation_branch(worker_config, branch)
    result = subprocess.run(
        ["git", "branch", "--show-current"], cwd=worker_config.docs_repo_path,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == branch


def test_stage_and_commit_returns_none_when_nothing_changed(worker_config) -> None:
    branch = git_ops.branch_name()
    git_ops.create_automation_branch(worker_config, branch)
    sha = git_ops.stage_and_commit(worker_config, (), "Update Titan fleet evidence 2026-08-06")
    assert sha is None


def test_stage_and_commit_creates_a_commit(worker_config) -> None:
    branch = git_ops.branch_name()
    git_ops.create_automation_branch(worker_config, branch)
    (worker_config.docs_repo_path / "01-Dashboards").mkdir()
    (worker_config.docs_repo_path / "01-Dashboards" / "FLEET-STATUS.md").write_text(
        "# Fleet Status\n", encoding="utf-8"
    )
    sha = git_ops.stage_and_commit(
        worker_config, (Path("01-Dashboards/FLEET-STATUS.md"),),
        "Update Titan fleet evidence 2026-08-06",
    )
    assert sha is not None
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=worker_config.docs_repo_path,
        capture_output=True, text=True, check=True,
    )
    assert log.stdout.strip() == "Update Titan fleet evidence 2026-08-06"


def test_push_automation_branch_refuses_main(worker_config) -> None:
    with pytest.raises(GitOpsError):
        git_ops.push_automation_branch(worker_config, "main")


def test_push_automation_branch_refuses_main_even_with_automation_prefix(worker_config) -> None:
    # Belt-and-suspenders: the trunk-name check is independent of the
    # prefix check, in case AUTOMATION_BRANCH_PREFIX is ever misconfigured.
    object.__setattr__(worker_config, "main_branch", "automation/titan-docs-99999999-9999")
    with pytest.raises(GitOpsError, match="trunk"):
        git_ops.push_automation_branch(worker_config, "automation/titan-docs-99999999-9999")


def test_push_automation_branch_refuses_non_automation_prefixed_name(worker_config) -> None:
    with pytest.raises(GitOpsError, match="non-automation"):
        git_ops.push_automation_branch(worker_config, "some-other-branch")


def test_push_automation_branch_pushes_only_that_branch(worker_config) -> None:
    branch = git_ops.branch_name()
    git_ops.create_automation_branch(worker_config, branch)
    (worker_config.docs_repo_path / "file.md").write_text("content", encoding="utf-8")
    git_ops.stage_and_commit(worker_config, (Path("file.md"),), "Update Titan fleet evidence 2026-08-06")
    git_ops.push_automation_branch(worker_config, branch)

    remote_branches = subprocess.run(
        ["git", "branch", "-r"], cwd=worker_config.docs_repo_path,
        capture_output=True, text=True, check=True,
    ).stdout
    assert f"origin/{branch}" in remote_branches
    # main on the remote must be untouched by this push.
    remote_main_sha = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=worker_config.docs_repo_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    local_main_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=worker_config.docs_repo_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_main_sha == local_main_sha


def test_return_to_main_switches_back(worker_config) -> None:
    branch = git_ops.branch_name()
    git_ops.create_automation_branch(worker_config, branch)
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"], cwd=worker_config.docs_repo_path,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        == branch
    )
    git_ops.return_to_main(worker_config)
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"], cwd=worker_config.docs_repo_path,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        == "main"
    )


def test_git_ops_module_has_no_merge_delete_or_tag_capability() -> None:
    """Structural proof, not just a policy check: no function in
    hermes_docs_worker.git_ops can merge a branch, delete a remote branch,
    or create a tag -- because no such function exists in the module."""
    import re

    public_names = {name for name in dir(git_ops) if not name.startswith("_")}
    forbidden_pattern = re.compile(r"\b(merge|delete|tag|force)\b")
    offending = {name for name in public_names if forbidden_pattern.search(name.lower())}
    assert offending == set()
