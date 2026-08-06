"""GitHub pull request creation, via ``gh``, behind a swappable client.

Structurally cannot merge, close, or approve a pull request: this module
defines exactly two client operations (:meth:`GitHubClient.find_open_pr`,
:meth:`GitHubClient.create_pr`) and neither the protocol nor
:class:`GhCliClient` implements anything else against the GitHub API. A test
double only needs to satisfy the same two-method protocol (see
``tests/hermes_docs_worker/fakes.py``), which is also what keeps this
module honest -- there is no back door in the real client that a fake
wouldn't also have to expose.
"""

from __future__ import annotations

import json
from typing import Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from hermes_docs_worker.config import AUTOMATION_BRANCH_PREFIX, DocsWorkerConfig
from hermes_docs_worker.proc import run_argv


class GitHubPRError(RuntimeError):
    """A GitHub PR lookup or creation failed."""


class PullRequestInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(..., ge=1)
    url: str = Field(..., min_length=1, max_length=512)
    head_branch: str = Field(..., min_length=1, max_length=256)
    state: str = Field(..., min_length=1, max_length=32)


class GitHubClient(Protocol):
    def find_open_pr(self, *, head_branch_prefix: str) -> Optional[PullRequestInfo]:
        """The most recent open PR whose head branch starts with
        ``head_branch_prefix``, or ``None`` if there isn't one."""

    def create_pr(
        self, *, head_branch: str, base_branch: str, title: str, body: str,
        labels: Sequence[str],
    ) -> PullRequestInfo:
        """Open a new PR. Must not merge, approve, or close anything."""


class GhCliClient:
    """Real client: shells out to the GitHub CLI (``gh``).

    Every call passes ``--repo config.github_repo`` explicitly, so this
    client talks to *that* GitHub repository regardless of what
    ``docs_repo_path``'s local git remotes are configured to. This is
    intentional in production (the checkout's ``origin`` should already be
    that same repo), but it means a locally authenticated ``gh`` will
    reach the real, live repository the moment ``create_pr``/``find_open_pr``
    are called with a real ``github_repo`` value -- there is no sandboxed
    or offline mode. Tests and any non-production dry run must use a fake
    :class:`GitHubClient` (never this class) unless the target is a
    disposable test repository.
    """

    def __init__(self, config: DocsWorkerConfig) -> None:
        self._config = config

    def find_open_pr(self, *, head_branch_prefix: str) -> Optional[PullRequestInfo]:
        result = run_argv(
            (
                "gh", "pr", "list",
                "--repo", self._config.github_repo,
                "--state", "open",
                "--json", "number,url,headRefName,state",
                "--limit", "50",
            ),
            cwd=self._config.docs_repo_path,
            timeout=self._config.max_subprocess_seconds,
        )
        if result.returncode != 0:
            raise GitHubPRError(f"gh pr list failed: {result.stderr.strip()}")
        try:
            entries = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as error:
            raise GitHubPRError("gh pr list returned unparseable JSON") from error

        matches = [e for e in entries if str(e.get("headRefName", "")).startswith(head_branch_prefix)]
        if not matches:
            return None
        matches.sort(key=lambda e: e.get("number", 0), reverse=True)
        top = matches[0]
        return PullRequestInfo(
            number=top["number"], url=top["url"], head_branch=top["headRefName"],
            state=top["state"],
        )

    def create_pr(
        self, *, head_branch: str, base_branch: str, title: str, body: str,
        labels: Sequence[str],
    ) -> PullRequestInfo:
        args = [
            "gh", "pr", "create",
            "--repo", self._config.github_repo,
            "--head", head_branch,
            "--base", base_branch,
            "--title", title,
            "--body", body,
        ]
        for label in labels:
            args += ["--label", label]
        result = run_argv(
            tuple(args), cwd=self._config.docs_repo_path,
            timeout=self._config.max_subprocess_seconds,
        )
        if result.returncode != 0:
            raise GitHubPRError(f"gh pr create failed: {result.stderr.strip()}")

        info = self.find_open_pr(head_branch_prefix=head_branch)
        if info is None:
            raise GitHubPRError("gh pr create reported success but the PR was not found")
        return info


def find_existing_titan_pr(client: GitHubClient) -> Optional[PullRequestInfo]:
    return client.find_open_pr(head_branch_prefix=AUTOMATION_BRANCH_PREFIX)
