"""The worker pipeline: collect, normalize, generate, and (if warranted)
commit, push, and open a PR.

Every mutating step is gated, in order: the single-run lock
(:mod:`hermes_docs_worker.locking`), the repository guard
(:mod:`hermes_docs_worker.repo_sync`), the existing-open-PR dedupe check
(:mod:`hermes_docs_worker.github_pr`), the no-change check
(:mod:`hermes_docs_worker.significance`), and the resource/diff/PR-frequency
budgets (:mod:`hermes_docs_worker.budgets`). ``dry_run=True`` stops the
pipeline immediately after Markdown generation -- no branch, commit, push,
or PR is ever created in a dry run, regardless of what the evidence says.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from hermes_docs_worker import git_ops, markdown_gen, repo_sync, wikilinks
from hermes_docs_worker.budgets import (
    BudgetExceededError,
    RunDeadline,
    check_diff_budget,
    check_pr_frequency,
    diff_stats_for_paths,
)
from hermes_docs_worker.collectors import (
    fleet_status,
    git_state,
    hermes_runtime,
    ollama_state,
    system_health,
    systemd_state,
    vault_contradictions,
    worker_config_evidence,
)
from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.contradiction import Contradiction, detect_status_contradictions
from hermes_docs_worker.evidence import (
    EvidenceFact,
    EvidenceRetentionStore,
    EvidenceSnapshot,
    make_run_id,
    now_epoch,
)
from hermes_docs_worker.github_pr import GhCliClient, GitHubClient, find_existing_titan_pr
from hermes_docs_worker.locking import AlreadyRunningError, LockUnavailableError, run_lock
from hermes_docs_worker.ollama_client import ProseClient, resolve_prose_client
from hermes_docs_worker.provenance import (
    provenance_entries_from_facts,
    render_source_provenance_update,
)
from hermes_docs_worker.redaction import redact_text
from hermes_docs_worker.significance import any_content_changed

logger = logging.getLogger("hermes.docs_worker.orchestrator")

RunMode = str  # "collect" | "daily" | "weekly"


@dataclass
class RunResult:
    run_id: str
    mode: RunMode
    dry_run: bool
    skipped_reason: Optional[str] = None
    fact_count: int = 0
    contradiction_count: int = 0
    changed_files: tuple[str, ...] = ()
    broken_wikilinks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    committed_sha: Optional[str] = None
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    errors: tuple[str, ...] = ()

    def summary(self) -> str:
        if self.skipped_reason:
            return f"run {self.run_id} ({self.mode}): skipped — {self.skipped_reason}"
        if self.dry_run:
            return (
                f"run {self.run_id} ({self.mode}): dry-run, {self.fact_count} facts, "
                f"{len(self.changed_files)} file(s) would change"
            )
        if self.pr_url:
            return f"run {self.run_id} ({self.mode}): opened {self.pr_url}"
        if self.committed_sha:
            return f"run {self.run_id} ({self.mode}): committed {self.committed_sha} (no PR yet)"
        return f"run {self.run_id} ({self.mode}): no documentation change"


def collect_evidence(config: DocsWorkerConfig, *, now: int) -> EvidenceSnapshot:
    """Run every collector, catching per-collector failures so one broken
    collector never aborts the whole snapshot."""
    facts: list[EvidenceFact] = []
    errors: list[str] = []

    collector_calls: Sequence[tuple[str, Callable[[], Sequence[EvidenceFact]]]] = (
        ("system_health", lambda: system_health.collect(now=now)),
        ("systemd_state", lambda: systemd_state.collect(config, now=now)),
        (
            "ollama_state",
            lambda: ollama_state.collect(
                endpoint=config.ollama_endpoint,
                timeout_seconds=config.ollama_timeout_seconds,
                now=now,
            ),
        ),
        ("hermes_runtime", lambda: hermes_runtime.collect(config, now=now)),
        ("worker_config_evidence", lambda: worker_config_evidence.collect(config, now=now)),
        ("git_state", lambda: git_state.collect(config, now=now)),
        ("fleet_status", lambda: fleet_status.collect(config, now=now)),
    )
    for name, call in collector_calls:
        try:
            facts.extend(call())
        except Exception as error:  # noqa: BLE001 - one collector must never abort the run
            errors.append(redact_text(f"{name} collector failed: {error}"))

    return EvidenceSnapshot(
        run_id=make_run_id(now), collected_at=now, facts=tuple(facts),
        collector_errors=tuple(errors),
    )


def _generate_documents(
    config: DocsWorkerConfig,
    snapshot: EvidenceSnapshot,
    contradictions: Sequence[Contradiction],
    *,
    mode: RunMode,
    prose_client: ProseClient,
    now: int,
    vault_items: Sequence = (),
) -> dict[Path, str]:
    documents: dict[Path, str] = {
        Path("01-Dashboards/FLEET-STATUS.md"): markdown_gen.render_fleet_status(
            snapshot.facts, generated_at=now
        ),
        Path("01-Dashboards/OPERATIONS-DASHBOARD.md"): markdown_gen.render_operations_dashboard(
            snapshot.facts, contradictions, generated_at=now, vault_contradictions=vault_items,
        ),
        Path(
            "09-Evidence/FLEET-VERIFICATION-MATRIX.md"
        ): markdown_gen.render_verification_matrix(snapshot.facts, generated_at=now),
        Path("09-Evidence/TITAN-DAILY-EVIDENCE.md"): markdown_gen.render_daily_evidence(
            snapshot, generated_at=now
        ),
    }

    if mode in ("daily", "weekly"):
        date_str = time.strftime("%Y-%m-%d", time.gmtime(now))
        prompt = (
            "Write a brief (2-4 sentence), conservative operational summary for a "
            "fleet documentation report. Only describe what the evidence below "
            "supports; never claim something is live/deployed unless its status "
            "says so.\n\nEvidence:\n"
            + "\n".join(
                f"- {f.category}/{f.label}: {f.status.value} ({f.detail})"
                for f in snapshot.facts
            )
        )
        prose = prose_client.generate(prompt)
        documents[Path(f"01-Daily/{date_str}-TITAN-CITY-REPORT.md")] = (
            markdown_gen.render_daily_city_report(
                snapshot, generated_at=now, prose=prose, date_str=date_str
            )
        )

    return documents


def _provenance_path(config: DocsWorkerConfig) -> Path:
    return config.docs_repo_path / "SOURCE-PROVENANCE.md"


def run_worker(
    config: DocsWorkerConfig,
    *,
    mode: RunMode,
    dry_run: bool,
    github_client_factory: Callable[[DocsWorkerConfig], GitHubClient] = GhCliClient,
    prose_client: Optional[ProseClient] = None,
    now: Optional[int] = None,
) -> RunResult:
    if mode not in ("collect", "daily", "weekly"):
        raise ValueError(f"unknown run mode {mode!r}")

    observed_at = now if now is not None else now_epoch()
    run_id = make_run_id(observed_at)
    deadline = RunDeadline(config.max_run_seconds)

    try:
        with run_lock(config.state_dir / "run.lock"):
            return _run_locked(
                config, mode=mode, dry_run=dry_run, run_id=run_id, observed_at=observed_at,
                deadline=deadline, github_client_factory=github_client_factory,
                prose_client=prose_client,
            )
    except AlreadyRunningError as error:
        return RunResult(run_id=run_id, mode=mode, dry_run=dry_run, skipped_reason=str(error))
    except LockUnavailableError as error:
        return RunResult(
            run_id=run_id, mode=mode, dry_run=dry_run,
            skipped_reason=f"lock unavailable: {error}",
        )


def _run_locked(
    config: DocsWorkerConfig,
    *,
    mode: RunMode,
    dry_run: bool,
    run_id: str,
    observed_at: int,
    deadline: RunDeadline,
    github_client_factory: Callable[[DocsWorkerConfig], GitHubClient],
    prose_client: Optional[ProseClient],
) -> RunResult:
    try:
        repo_sync.ensure_repo_synced(config)
    except repo_sync.RepoGuardError as error:
        return RunResult(run_id=run_id, mode=mode, dry_run=dry_run, skipped_reason=str(error))

    github_client = github_client_factory(config)
    if not dry_run:
        try:
            existing = find_existing_titan_pr(github_client)
        except Exception as error:  # noqa: BLE001 - unreachable GitHub must fail closed, not crash
            return RunResult(
                run_id=run_id, mode=mode, dry_run=dry_run,
                skipped_reason=f"could not check for an existing automation PR: {error}",
            )
        if existing is not None:
            return RunResult(
                run_id=run_id, mode=mode, dry_run=dry_run,
                skipped_reason=(
                    f"an automation PR is already open awaiting review: {existing.url}"
                ),
            )

    snapshot = collect_evidence(config, now=observed_at)
    deadline.ensure_not_expired()

    contradictions = detect_status_contradictions(snapshot.facts)
    vault_items = (
        vault_contradictions.collect(config.docs_repo_path) if mode == "weekly" else ()
    )
    resolved_prose_client = prose_client or resolve_prose_client(
        endpoint=config.ollama_endpoint, model=config.ollama_model,
        timeout_seconds=config.ollama_timeout_seconds,
    )
    documents = _generate_documents(
        config, snapshot, contradictions, mode=mode, prose_client=resolved_prose_client,
        now=observed_at, vault_items=vault_items,
    )

    if mode == "weekly":
        previous_provenance = ""
        provenance_path = _provenance_path(config)
        if provenance_path.exists():
            previous_provenance = provenance_path.read_text(encoding="utf-8")
        documents[Path("SOURCE-PROVENANCE.md")] = render_source_provenance_update(
            previous_provenance,
            provenance_entries_from_facts(snapshot.facts),
            generated_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(observed_at)),
            run_id=run_id,
        )

    for contradiction in contradictions:
        incident_relative = Path(
            f"00-Inbox/incidents/{run_id}-{contradiction.category}-{contradiction.label}.md"
        )
        documents[incident_relative] = markdown_gen.render_incident_draft(
            contradiction, generated_at=observed_at, run_id=run_id
        )

    broken_links = wikilinks.validate_generated_content(documents, config.docs_repo_path)

    retention_store = EvidenceRetentionStore(config.state_dir)
    retention_store.append(
        snapshot, retention_days=config.evidence_retention_days,
        max_files=config.evidence_max_files,
    )

    changed = {
        path: content for path, content in documents.items()
        if any_content_changed({path: content}, config.docs_repo_path)
    }
    result = RunResult(
        run_id=run_id, mode=mode, dry_run=dry_run, fact_count=len(snapshot.facts),
        contradiction_count=len(contradictions),
        changed_files=tuple(str(p) for p in sorted(changed)),
        broken_wikilinks={str(p): v for p, v in broken_links.items()},
        errors=snapshot.collector_errors,
    )

    if not changed:
        result.skipped_reason = "no meaningful documentation change"
        return result

    if dry_run:
        return result

    deadline.ensure_not_expired()
    try:
        check_diff_budget(diff_stats_for_paths(changed), config)
    except BudgetExceededError as error:
        result.skipped_reason = f"budget exceeded: {error}"
        return result

    for relative_path, content in changed.items():
        if relative_path.is_absolute() or ".." in relative_path.parts:
            result.skipped_reason = (
                f"refusing to write outside the documentation checkout: {relative_path}"
            )
            return result
        target = config.docs_repo_path / relative_path
        if not config.is_within_allowlist(target):
            result.skipped_reason = (
                f"refusing to write outside the filesystem allowlist: {target}"
            )
            return result
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    branch = git_ops.branch_name(now=observed_at)
    message = git_ops.commit_message(now=observed_at)
    branch_created = False
    try:
        try:
            git_ops.create_automation_branch(config, branch)
            branch_created = True
            commit_sha = git_ops.stage_and_commit(config, tuple(changed.keys()), message)
        except git_ops.GitOpsError as error:
            result.skipped_reason = f"git operation failed: {error}"
            return result

        if commit_sha is None:
            result.skipped_reason = "no meaningful documentation change (nothing staged)"
            return result
        result.committed_sha = commit_sha
        result.branch = branch

        try:
            git_ops.push_automation_branch(config, branch)
        except git_ops.GitOpsError as error:
            result.skipped_reason = f"push failed: {error}"
            return result

        try:
            check_pr_frequency(
                last_pr_opened_at=_last_pr_opened_at(config), now=time.time(), config=config
            )
            pr = github_client.create_pr(
                head_branch=branch, base_branch=config.main_branch,
                title=message,
                body=_pr_body(snapshot, contradictions, changed, broken_links),
                labels=config.pr_labels,
            )
        except Exception as error:  # noqa: BLE001 - a PR failure must not crash the run
            result.skipped_reason = f"PR creation skipped: {error}"
            return result

        result.pr_url = pr.url
        _record_pr_opened_at(config, time.time())
        return result
    finally:
        # Always leave the checkout back on main, regardless of how this
        # run ended, so the next scheduled run's repo_sync guard (which
        # requires being on main) doesn't fail closed because of a branch
        # this run left checked out.
        if branch_created:
            try:
                git_ops.return_to_main(config)
            except git_ops.GitOpsError as error:
                logger.warning("could not return documentation checkout to main: %s", error)


def _pr_body(
    snapshot: EvidenceSnapshot, contradictions: Sequence[Contradiction],
    changed: dict[Path, str], broken_links: dict[Path, tuple[str, ...]],
) -> str:
    lines = [
        f"Automated Titan fleet evidence update (run `{snapshot.run_id}`).",
        "",
        f"- Facts collected: {len(snapshot.facts)}",
        f"- Collector errors: {len(snapshot.collector_errors)}",
        f"- Contradictions detected: {len(contradictions)}",
        f"- Files changed: {len(changed)}",
        "",
        "## Files changed",
        "",
    ]
    lines += [f"- `{p}`" for p in sorted(str(p) for p in changed)]
    lines += ["", "## Wiki-link validation", ""]
    if broken_links:
        for path, missing in broken_links.items():
            lines.append(f"- `{path}`: broken link(s) {', '.join(missing)}")
    else:
        lines.append("All wiki-links in changed documents resolve.")
    lines += [
        "",
        "This PR was opened by the governed Titan documentation worker. It has not "
        "merged anything and cannot merge itself — review required.",
    ]
    return redact_text("\n".join(lines))


def _pr_state_path(config: DocsWorkerConfig) -> Path:
    return config.state_dir / "last_pr_opened_at.txt"


def _last_pr_opened_at(config: DocsWorkerConfig) -> Optional[float]:
    path = _pr_state_path(config)
    if not path.exists():
        return None
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _record_pr_opened_at(config: DocsWorkerConfig, when: float) -> None:
    path = _pr_state_path(config)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(str(when), encoding="utf-8")
