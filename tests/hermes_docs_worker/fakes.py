"""Fake GitHub and Ollama clients for tests -- no network, no ``gh`` binary,
no real Ollama server. Both satisfy the same protocol the real clients do
(see hermes_docs_worker.github_pr.GitHubClient and
hermes_docs_worker.ollama_client.ProseClient) so tests exercise the same
call shape production code does.
"""

from __future__ import annotations

from typing import Optional, Sequence

from hermes_docs_worker.github_pr import PullRequestInfo


class FakeGitHubClient:
    def __init__(self, *, existing_open_pr: Optional[PullRequestInfo] = None) -> None:
        self.existing_open_pr = existing_open_pr
        self.created_prs: list[dict] = []
        self.merge_calls = 0
        self.close_calls = 0

    def find_open_pr(self, *, head_branch_prefix: str) -> Optional[PullRequestInfo]:
        if self.existing_open_pr and self.existing_open_pr.head_branch.startswith(head_branch_prefix):
            return self.existing_open_pr
        return None

    def create_pr(
        self, *, head_branch: str, base_branch: str, title: str, body: str,
        labels: Sequence[str],
    ) -> PullRequestInfo:
        self.created_prs.append(
            {"head": head_branch, "base": base_branch, "title": title, "body": body, "labels": tuple(labels)}
        )
        info = PullRequestInfo(
            number=len(self.created_prs), url=f"https://example.invalid/pr/{len(self.created_prs)}",
            head_branch=head_branch, state="open",
        )
        self.existing_open_pr = info
        return info

    # Deliberately absent: merge_pr / close_pr / delete_branch / tag_release.
    # A test that tries to call one of these must fail with AttributeError,
    # proving the capability doesn't exist anywhere in this codebase's
    # GitHub interaction surface, real or fake.


class FakeProseClient:
    def __init__(self, response: Optional[str] = "A conservative, evidence-based summary.") -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> Optional[str]:
        self.prompts.append(prompt)
        return self.response
