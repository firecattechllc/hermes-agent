from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .ports import (
    HermesAnalysisPort,
    HermesAnalysisResult,
    HermesEvidencePort,
    HermesGraphPort,
    HermesMemoryPort,
)


@dataclass(slots=True)
class HermesClient:
    graph: HermesGraphPort
    memory: HermesMemoryPort
    analysis: HermesAnalysisPort
    evidence: HermesEvidencePort


class InMemoryHermesAdapter(
    HermesGraphPort,
    HermesMemoryPort,
    HermesAnalysisPort,
    HermesEvidencePort,
):
    """Deterministic adapter for tests and local development only."""

    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    def company_context(self, *, company: str, ticker: str | None) -> Mapping[str, Any]:
        return {"company": company, "ticker": ticker, "source": "hermes-graph-test-adapter"}

    def recall_company(self, *, company: str, ticker: str | None) -> Sequence[Mapping[str, Any]]:
        return ()

    def analyze_company(
        self,
        *,
        company: str,
        ticker: str | None,
        questions: Sequence[str],
        graph_context: Mapping[str, Any],
        memory_context: Sequence[Mapping[str, Any]],
    ) -> HermesAnalysisResult:
        symbol = ticker or company
        return HermesAnalysisResult(
            thesis=f"Research workflow initialized for {symbol} through governed Hermes services.",
            risks=("Production data providers are not connected in Step 1.",),
            catalysts=("Hermes-native financial data integration", "FinBERT sentiment pipeline"),
            confidence="foundation-only",
            evidence_payloads=(
                {
                    "kind": "workflow_execution",
                    "company": company,
                    "ticker": ticker,
                    "questions": list(questions),
                    "graph_context": dict(graph_context),
                    "memory_items": len(memory_context),
                },
            ),
        )

    def record(self, *, kind: str, payload: Mapping[str, Any]) -> str:
        evidence_id = f"sigil-evidence-{len(self.recorded) + 1}"
        self.recorded.append({"id": evidence_id, "kind": kind, "payload": dict(payload)})
        return evidence_id
