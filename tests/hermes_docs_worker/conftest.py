from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_docs_worker.config import DocsWorkerConfig


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init", "-q", "-b", branch)
    _run_git(path, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-q", "--allow-empty", "-m", "init")
    return path


def add_bare_remote(repo: Path, remote_dir: Path, *, name: str = "origin") -> Path:
    _run_git(remote_dir.parent, "init", "-q", "--bare", str(remote_dir))
    _run_git(repo, "remote", "add", name, str(remote_dir))
    _run_git(repo, "push", "-q", "-u", name, "HEAD")
    return remote_dir


@pytest.fixture
def hermes_source_dir(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "hermes-source")


@pytest.fixture
def docs_repo(tmp_path: Path) -> Path:
    repo = init_repo(tmp_path / "docs-repo")
    add_bare_remote(repo, tmp_path / "origin.git")
    return repo


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    path = tmp_path / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def worker_config(hermes_source_dir: Path, docs_repo: Path, state_dir: Path) -> DocsWorkerConfig:
    return DocsWorkerConfig(
        hermes_source_dir=hermes_source_dir,
        docs_repo_path=docs_repo,
        state_dir=state_dir,
        github_repo="test-org/hydra-docs-test",
        git_remote_name="origin",
        main_branch="main",
        git_user_name="Test Worker",
        git_user_email="worker@example.com",
        ollama_endpoint="http://127.0.0.1:1",
        ollama_model="gemma3:4b",
        ollama_timeout_seconds=1,
        systemd_allowlist=("hermes-docs-evidence.service",),
        extra_filesystem_allowlist=(),
        max_diff_bytes=200_000,
        max_files_changed=25,
        min_pr_interval_seconds=0,
        max_run_seconds=60,
        max_subprocess_seconds=10,
        evidence_retention_days=30,
        evidence_max_files=500,
        pr_labels=("automation", "titan-docs"),
        fleet_node_keys=("prime",),
    )
