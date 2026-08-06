"""Contradiction detection.

Two sources of contradictions are merged into one report:

1. **Cross-fact contradictions** within a single evidence snapshot: two
   collectors disagreeing about the status of the same ``(category,
   label)`` subject (:func:`detect_status_contradictions`).
2. **Vault-declared contradictions**: unresolved contradiction notes
   already tracked in the documentation vault itself
   (:mod:`hermes_docs_worker.collectors.vault_contradictions` reads these;
   this module only defines the shared record shape and the merge).

Detecting a contradiction never resolves it -- the worker's job is to
surface it (in the verification matrix, and as an incident draft for a new
severe one) for a human to reconcile, never to silently pick a winner.
"""

from __future__ import annotations

from typing import Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hermes_docs_worker.evidence import EvidenceFact
from hermes_docs_worker.redaction import assert_redacted


class Contradiction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=512)
    sources: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _redacted(self) -> "Contradiction":
        assert_redacted(self.description, field_name="description")
        return self


class VaultContradiction(BaseModel):
    """An unresolved contradiction already recorded in the vault (read-only
    -- this worker never authors the resolution, only surfaces it)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vault_path: str = Field(..., min_length=1, max_length=512)
    title: str = Field(..., min_length=1, max_length=256)


def detect_status_contradictions(facts: Sequence[EvidenceFact]) -> Tuple[Contradiction, ...]:
    """Flag any ``(category, label)`` subject with more than one distinct
    status reported by different sources in the same snapshot."""
    grouped: dict[tuple[str, str], dict[str, set[str]]] = {}
    for fact in facts:
        key = fact.key()
        by_status = grouped.setdefault(key, {})
        by_status.setdefault(fact.status.value, set()).add(fact.source)

    contradictions: list[Contradiction] = []
    for (category, label), by_status in sorted(grouped.items()):
        if len(by_status) <= 1:
            continue
        statuses_desc = ", ".join(
            f"{status} (via {', '.join(sorted(sources))})"
            for status, sources in sorted(by_status.items())
        )
        all_sources = tuple(sorted({s for sources in by_status.values() for s in sources}))
        contradictions.append(
            Contradiction(
                category=category,
                label=label,
                description=(
                    f"{category}/{label} was reported with conflicting statuses in the "
                    f"same run: {statuses_desc}"
                ),
                sources=all_sources,
            )
        )
    return tuple(contradictions)
