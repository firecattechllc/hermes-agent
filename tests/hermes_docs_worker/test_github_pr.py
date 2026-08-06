from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hermes_docs_worker.config import AUTOMATION_BRANCH_PREFIX
from hermes_docs_worker.github_pr import GhCliClient, GitHubPRError, find_existing_titan_pr
from tests.hermes_docs_worker.fakes import FakeGitHubClient


def test_find_existing_titan_pr_none_when_no_matching_branch() -> None:
    client = FakeGitHubClient()
    assert find_existing_titan_pr(client) is None


def test_find_existing_titan_pr_finds_matching_branch() -> None:
    from hermes_docs_worker.github_pr import PullRequestInfo

    existing = PullRequestInfo(
        number=1, url="https://example.invalid/pr/1",
        head_branch=f"{AUTOMATION_BRANCH_PREFIX}20260101-0000", state="open",
    )
    client = FakeGitHubClient(existing_open_pr=existing)
    found = find_existing_titan_pr(client)
    assert found is not None
    assert found.number == 1


def test_fake_github_client_has_no_merge_or_close_method() -> None:
    client = FakeGitHubClient()
    assert not hasattr(client, "merge_pr")
    assert not hasattr(client, "close_pr")
    assert not hasattr(client, "delete_branch")


def test_gh_cli_client_find_open_pr_parses_matching_entries(worker_config, monkeypatch) -> None:
    payload = json.dumps(
        [
            {"number": 5, "url": "https://example.invalid/pr/5", "headRefName": f"{AUTOMATION_BRANCH_PREFIX}20260101-0000", "state": "OPEN"},
            {"number": 2, "url": "https://example.invalid/pr/2", "headRefName": "unrelated-branch", "state": "OPEN"},
        ]
    )
    monkeypatch.setattr(
        "hermes_docs_worker.github_pr.run_argv",
        lambda argv, **kw: SimpleNamespace(returncode=0, stdout=payload, stderr=""),
    )
    client = GhCliClient(worker_config)
    found = client.find_open_pr(head_branch_prefix=AUTOMATION_BRANCH_PREFIX)
    assert found is not None
    assert found.number == 5
    assert found.head_branch.startswith(AUTOMATION_BRANCH_PREFIX)


def test_gh_cli_client_find_open_pr_none_when_no_match(worker_config, monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_docs_worker.github_pr.run_argv",
        lambda argv, **kw: SimpleNamespace(returncode=0, stdout="[]", stderr=""),
    )
    client = GhCliClient(worker_config)
    assert client.find_open_pr(head_branch_prefix=AUTOMATION_BRANCH_PREFIX) is None


def test_gh_cli_client_find_open_pr_raises_on_failure(worker_config, monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_docs_worker.github_pr.run_argv",
        lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="not authenticated"),
    )
    client = GhCliClient(worker_config)
    with pytest.raises(GitHubPRError):
        client.find_open_pr(head_branch_prefix=AUTOMATION_BRANCH_PREFIX)


def test_gh_cli_client_has_no_merge_close_or_delete_capability() -> None:
    import re

    public_names = {name for name in dir(GhCliClient) if not name.startswith("_")}
    forbidden = re.compile(r"\b(merge|close|delete|approve)\b")
    offending = {name for name in public_names if forbidden.search(name.lower())}
    assert offending == set()
