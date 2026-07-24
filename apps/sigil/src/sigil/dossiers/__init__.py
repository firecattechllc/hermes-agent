"""Governed immutable research dossier engine."""

from .comparison import ResearchDossierComparison, compare_dossiers
from .engine import (
    DossierInput,
    build_dossier,
    claim_to_evidence,
    evidence_coverage,
    evidence_to_claims,
    inspect_conclusion,
    list_conflicts,
    list_gaps,
    list_questions,
    list_sections,
    list_stale_evidence,
    verify_dossier_identity,
)
from .financials import cagr, classify_trend, growth, ratio, subtract
from .models import *
from .policy import ResearchDossierPolicy

__all__ = [
    "DossierInput",
    "ResearchDossierComparison",
    "ResearchDossierPolicy",
    "build_dossier",
    "cagr",
    "claim_to_evidence",
    "classify_trend",
    "compare_dossiers",
    "evidence_coverage",
    "evidence_to_claims",
    "growth",
    "inspect_conclusion",
    "list_conflicts",
    "list_gaps",
    "list_questions",
    "list_sections",
    "list_stale_evidence",
    "ratio",
    "subtract",
    "verify_dossier_identity",
]
