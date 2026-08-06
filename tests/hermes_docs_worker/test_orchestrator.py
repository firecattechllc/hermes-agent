from __future__ import annotations

import subprocess

from hermes_docs_worker.evidence import EvidenceFact
from hermes_docs_worker.orchestrator import run_worker
from hermes_docs_worker.status import StatusValue
from tests.hermes_docs_worker.fakes import FakeGitHubClient, FakeProseClient


def _no_collectors(monkeypatch, module_path: str) -> None:
    """Neutralize a collector that would otherwise shell out to a binary
    this dev/CI environment may not have (systemctl)."""
    monkeypatch.setattr(f"{module_path}.collect", lambda *a, **kw: ())


def _quiet_environment(monkeypatch) -> None:
    # These collectors would otherwise try systemctl (absent on macOS/most
    # CI containers) or a real fleet registry file; keep orchestrator tests
    # focused on orchestration behavior, not on every collector succeeding.
    monkeypatch.setattr("hermes_docs_worker.orchestrator.systemd_state.collect", lambda *a, **kw: ())
    monkeypatch.setattr("hermes_docs_worker.orchestrator.hermes_runtime.collect", lambda *a, **kw: ())
    monkeypatch.setattr("hermes_docs_worker.orchestrator.fleet_status.collect", lambda *a, **kw: ())
    monkeypatch.setattr(
        "hermes_docs_worker.orchestrator.ollama_state.collect",
        lambda **kw: (
            EvidenceFact(
                category="ollama", label="reachability", status=StatusValue.DEGRADED,
                detail="unreachable in test", source="ollama_state", collected_at=kw.get("now", 0),
            ),
        ),
    )


def test_dry_run_never_commits_pushes_or_opens_a_pr(worker_config, monkeypatch) -> None:
    _quiet_environment(monkeypatch)
    github = FakeGitHubClient()
    result = run_worker(
        worker_config, mode="collect", dry_run=True,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert result.dry_run is True
    assert result.committed_sha is None
    assert result.pr_url is None
    assert github.created_prs == []

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=worker_config.docs_repo_path,
        capture_output=True, text=True, check=True,
    )
    assert status.stdout.strip() == ""  # nothing written to disk in a dry run
    branches = subprocess.run(
        ["git", "branch"], cwd=worker_config.docs_repo_path, capture_output=True, text=True, check=True,
    ).stdout
    assert "automation/titan-docs-" not in branches


def test_real_run_commits_pushes_and_opens_a_pr(worker_config, monkeypatch) -> None:
    _quiet_environment(monkeypatch)
    github = FakeGitHubClient()
    result = run_worker(
        worker_config, mode="collect", dry_run=False,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert result.committed_sha is not None
    assert result.pr_url is not None
    assert len(github.created_prs) == 1
    assert github.created_prs[0]["head"] == result.branch
    assert github.created_prs[0]["head"].startswith("automation/titan-docs-")


def test_second_run_with_unchanged_evidence_is_idempotent(worker_config, monkeypatch) -> None:
    # Fully deterministic evidence (fixed facts, fixed `now`) so two runs
    # produce byte-identical Markdown -- the only way to isolate "the
    # generated content didn't change" from "the underlying live evidence
    # happened to differ this second," which it always will in production.
    fixed_facts = (
        EvidenceFact(
            category="system_health", label="disk", status=StatusValue.VERIFIED,
            detail="10% used", source="system_health", collected_at=1_800_000_000,
        ),
    )
    monkeypatch.setattr("hermes_docs_worker.orchestrator.system_health.collect", lambda **kw: fixed_facts)
    monkeypatch.setattr("hermes_docs_worker.orchestrator.worker_config_evidence.collect", lambda cfg, **kw: ())
    monkeypatch.setattr("hermes_docs_worker.orchestrator.git_state.collect", lambda cfg, **kw: ())
    _quiet_environment(monkeypatch)

    github = FakeGitHubClient()
    first = run_worker(
        worker_config, mode="collect", dry_run=False, now=1_800_000_000,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert first.committed_sha is not None

    # Simulate a human merging the reviewed automation PR into main -- this
    # worker never does this itself (see test_git_ops.py's structural
    # proof), but the idempotency gate is specifically about *main already
    # having this content*, not about "ran twice in a row."
    subprocess.run(
        ["git", "merge", "--no-ff", "-m", "merge automation PR", first.branch],
        cwd=worker_config.docs_repo_path, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=worker_config.docs_repo_path, check=True, capture_output=True, text=True,
    )
    github.existing_open_pr = None  # the merged PR is no longer open

    second = run_worker(
        worker_config, mode="collect", dry_run=False, now=1_800_000_000,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert second.skipped_reason == "no meaningful documentation change"
    assert second.committed_sha is None
    assert len(github.created_prs) == 1  # unchanged from the first run


def test_existing_open_pr_blocks_a_new_run(worker_config, monkeypatch) -> None:
    from hermes_docs_worker.config import AUTOMATION_BRANCH_PREFIX
    from hermes_docs_worker.github_pr import PullRequestInfo

    _quiet_environment(monkeypatch)
    existing = PullRequestInfo(
        number=1, url="https://example.invalid/pr/1",
        head_branch=f"{AUTOMATION_BRANCH_PREFIX}20260101-0000", state="open",
    )
    github = FakeGitHubClient(existing_open_pr=existing)
    result = run_worker(
        worker_config, mode="collect", dry_run=False,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert result.skipped_reason is not None
    assert "already open" in result.skipped_reason
    assert github.created_prs == []


def test_dirty_documentation_repo_fails_closed(worker_config, monkeypatch) -> None:
    _quiet_environment(monkeypatch)
    (worker_config.docs_repo_path / "dirty.md").write_text("uncommitted", encoding="utf-8")
    github = FakeGitHubClient()
    result = run_worker(
        worker_config, mode="collect", dry_run=False,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert result.skipped_reason is not None
    assert "uncommitted" in result.skipped_reason
    assert github.created_prs == []


def test_diff_budget_exceeded_blocks_the_commit(worker_config, monkeypatch) -> None:
    _quiet_environment(monkeypatch)
    object.__setattr__(worker_config, "max_files_changed", 1)
    github = FakeGitHubClient()
    result = run_worker(
        worker_config, mode="daily", dry_run=False,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert result.skipped_reason is not None
    assert "budget" in result.skipped_reason.lower()
    assert result.committed_sha is None
    assert github.created_prs == []


def test_pr_frequency_budget_blocks_a_second_pr_too_soon(worker_config, monkeypatch) -> None:
    _quiet_environment(monkeypatch)
    object.__setattr__(worker_config, "min_pr_interval_seconds", 3600)
    github = FakeGitHubClient()

    first = run_worker(
        worker_config, mode="collect", dry_run=False, now=1_800_000_000,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    assert first.pr_url is not None

    # Force a second meaningful change shortly after (different run, close in time).
    github.existing_open_pr = None  # simulate the first PR having been merged already
    second = run_worker(
        worker_config, mode="daily", dry_run=False, now=1_800_000_010,
        github_client_factory=lambda cfg: github, prose_client=FakeProseClient(),
    )
    if second.committed_sha is not None:
        assert second.pr_url is None
        assert "PR creation skipped" in (second.skipped_reason or "")


def test_generated_documents_never_contain_a_secret_end_to_end(worker_config, monkeypatch) -> None:
    _quiet_environment(monkeypatch)
    monkeypatch.setattr(
        "hermes_docs_worker.orchestrator.system_health.collect",
        lambda **kw: (
            EvidenceFact(
                category="system_health", label="disk", status=StatusValue.VERIFIED,
                detail="usage nominal", source="system_health", collected_at=kw.get("now", 0),
            ),
        ),
    )
    monkeypatch.setattr(
        "hermes_docs_worker.orchestrator.worker_config_evidence.collect",
        lambda cfg, **kw: (
            EvidenceFact(
                category="worker_config", label="validity", status=StatusValue.VERIFIED,
                detail="ok", source="worker_config_evidence", collected_at=kw.get("now", 0),
            ),
        ),
    )
    class _LeakyProse:
        """A prose client that -- unlike the real OllamaProseClient --
        does not redact its own output. This is the scenario
        markdown_gen's final defensive redact_text() pass exists for:
        even a misbehaving/compromised prose backend must not be able to
        get a secret into a committed document."""

        def generate(self, prompt: str) -> str:
            return "everything is fine, password: hunter2hunter2, token=abcdef1234567890, host 192.168.5.9"

    github = FakeGitHubClient()
    result = run_worker(
        worker_config, mode="daily", dry_run=False,
        github_client_factory=lambda cfg: github, prose_client=_LeakyProse(),
    )
    assert result.committed_sha is not None

    # The checkout is back on main by the time run_worker returns (see
    # git_ops.return_to_main), so read the committed content straight out
    # of the automation branch's commit rather than the working tree.
    for relative_path in result.changed_files:
        content = subprocess.run(
            ["git", "show", f"{result.committed_sha}:{relative_path}"],
            cwd=worker_config.docs_repo_path, capture_output=True, text=True, check=True,
        ).stdout
        assert "abcdef1234567890" not in content
        assert "hunter2hunter2" not in content
        assert "192.168.5.9" not in content
