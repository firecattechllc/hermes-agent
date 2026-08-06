from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import hermes_docs_worker.config as config_module
from hermes_docs_worker.config import DocsWorkerConfig, DocsWorkerConfigError


def _env(tmp_path: Path, **overrides) -> dict:
    base = {
        "HERMES_DOCS_WORKER_HERMES_SOURCE_DIR": str(tmp_path / "hermes-source"),
        "HERMES_DOCS_WORKER_DOCS_REPO_PATH": str(tmp_path / "docs-repo"),
        "HERMES_DOCS_WORKER_STATE_DIR": str(tmp_path / "state"),
        "HERMES_DOCS_WORKER_GITHUB_REPO": "test-org/hydra-docs-test",
    }
    base.update(overrides)
    return base


def test_from_env_parses_defaults(tmp_path: Path) -> None:
    env = {
        "HERMES_DOCS_WORKER_HERMES_SOURCE_DIR": str(tmp_path / "hermes-source"),
        "HERMES_DOCS_WORKER_GITHUB_REPO": "test-org/hydra-docs-test",
    }
    config = DocsWorkerConfig.from_env(env)
    assert config.docs_repo_path == Path("/opt/hermes-docs/hydra-docs")
    assert config.state_dir == Path("/var/lib/hermes-docs-worker")
    assert config.main_branch == "main"
    assert config.ollama_model == "gemma3:4b"


def test_from_env_requires_hermes_source_dir(tmp_path: Path) -> None:
    env = _env(tmp_path)
    del env["HERMES_DOCS_WORKER_HERMES_SOURCE_DIR"]
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(env)


def test_from_env_requires_github_repo(tmp_path: Path) -> None:
    env = _env(tmp_path)
    del env["HERMES_DOCS_WORKER_GITHUB_REPO"]
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(env)


def test_rejects_malformed_github_repo_slug(tmp_path: Path) -> None:
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(_env(tmp_path, HERMES_DOCS_WORKER_GITHUB_REPO="not-a-slug"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"HERMES_DOCS_WORKER_HERMES_SOURCE_DIR": "/Users/someone/hermes"},
        {"HERMES_DOCS_WORKER_OLLAMA_ENDPOINT": "http://matthews-macbook-air:11434"},
        {"HERMES_DOCS_WORKER_OLLAMA_ENDPOINT": "http://host.docker.internal:11434"},
    ],
)
def test_rejects_mac_dependency(tmp_path: Path, overrides: dict) -> None:
    with pytest.raises(DocsWorkerConfigError, match="Mac dependency"):
        DocsWorkerConfig.from_env(_env(tmp_path, **overrides))


def test_rejects_relative_paths(tmp_path: Path) -> None:
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(
            _env(tmp_path, HERMES_DOCS_WORKER_DOCS_REPO_PATH="relative/path")
        )


def test_rejects_duplicate_paths(tmp_path: Path) -> None:
    same = str(tmp_path / "shared")
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(
            _env(
                tmp_path,
                HERMES_DOCS_WORKER_HERMES_SOURCE_DIR=same,
                HERMES_DOCS_WORKER_DOCS_REPO_PATH=same,
            )
        )


def test_rejects_invalid_systemd_unit_names(tmp_path: Path) -> None:
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(
            _env(tmp_path, HERMES_DOCS_WORKER_SYSTEMD_ALLOWLIST="not_a_unit,also bad")
        )


def test_rejects_out_of_range_budgets(tmp_path: Path) -> None:
    with pytest.raises(DocsWorkerConfigError):
        DocsWorkerConfig.from_env(_env(tmp_path, HERMES_DOCS_WORKER_MAX_FILES_CHANGED="0"))


def test_is_within_allowlist(tmp_path: Path) -> None:
    config = DocsWorkerConfig.from_env(_env(tmp_path))
    assert config.is_within_allowlist(config.docs_repo_path / "foo.md")
    assert not config.is_within_allowlist(Path("/etc/passwd"))


def test_does_not_import_across_unmerged_omniroute_branch() -> None:
    """hermes_docs_worker must not depend on hermes_cli.prime.omniroute_config,
    which lives on a separate, not-yet-merged branch
    (feat/titan-omniroute-freellmapi). A cross-branch import here previously
    broke test collection for every Python CI slice with
    ModuleNotFoundError, since that module isn't present on this branch."""
    source = inspect.getsource(config_module)
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    assert not any("hermes_cli" in line for line in import_lines), import_lines
