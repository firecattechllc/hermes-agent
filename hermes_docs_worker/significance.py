"""Change significance scoring and no-change (idempotency) detection.

Two distinct questions live here:

1. *How significant* is the difference between this run's evidence and the
   previous run's (:func:`score_evidence_change`)? Used to decide whether a
   run warrants an incident draft or is routine.
2. *Did the generated Markdown actually change* (:func:`generated_content_changed`)?
   This is the authoritative "do not commit when generated output is
   unchanged" gate -- compared byte-for-byte against what's already on disk
   in the documentation checkout, not against the previous evidence
   snapshot, so a run that observes new evidence but produces
   byte-identical prose still correctly commits nothing.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

from hermes_docs_worker.evidence import EvidenceSnapshot


class ChangeSignificance(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


def score_evidence_change(
    previous: Optional[EvidenceSnapshot], current: EvidenceSnapshot
) -> ChangeSignificance:
    """Score how much the fleet's observed status changed between runs.

    A brand-new subject (never observed before) counts as a status change.
    Any status flip into or out of ``Blocked``/``Degraded`` is weighted
    more heavily than a flip among the calmer statuses, since that's the
    kind of change most likely to warrant an incident draft.
    """
    if previous is None:
        return ChangeSignificance.MAJOR if current.facts else ChangeSignificance.NONE

    previous_by_key = {fact.key(): fact.status for fact in previous.facts}
    current_by_key = {fact.key(): fact.status for fact in current.facts}

    all_keys = set(previous_by_key) | set(current_by_key)
    changed = 0
    severe = 0
    for key in all_keys:
        before = previous_by_key.get(key)
        after = current_by_key.get(key)
        if before == after:
            continue
        changed += 1
        severity_statuses = {"Blocked", "Degraded"}
        before_severe = before is not None and before.value in severity_statuses
        after_severe = after is not None and after.value in severity_statuses
        if before_severe != after_severe:
            severe += 1

    if changed == 0:
        return ChangeSignificance.NONE
    if severe > 0:
        return ChangeSignificance.MAJOR
    if changed >= max(3, len(all_keys) // 4):
        return ChangeSignificance.MODERATE
    return ChangeSignificance.MINOR


def generated_content_changed(
    generated: Mapping[Path, str], existing_repo_root: Path
) -> dict[Path, bool]:
    """For each generated ``(relative_path -> content)`` pair, whether it
    differs from what's currently on disk in the documentation checkout (a
    missing file counts as changed)."""
    result: dict[Path, bool] = {}
    for relative_path, content in generated.items():
        target = existing_repo_root / relative_path
        if not target.exists():
            result[relative_path] = True
            continue
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError:
            result[relative_path] = True
            continue
        result[relative_path] = existing != content
    return result


def any_content_changed(generated: Mapping[Path, str], existing_repo_root: Path) -> bool:
    return any(generated_content_changed(generated, existing_repo_root).values())
