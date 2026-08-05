from __future__ import annotations

from sigil.domain import CompanyResearchReport, CompanyResearchRequest, EvidenceReference
from sigil.integrations.hermes import HermesClient


class ResearchCompanyWorkflow:
    """First end-to-end Sigil workflow, coordinated through Hermes ports."""

    def __init__(self, hermes: HermesClient) -> None:
        self._hermes = hermes

    def run(self, request: CompanyResearchRequest) -> CompanyResearchReport:
        request = request.normalized()
        graph_context = self._hermes.graph.company_context(
            company=request.company,
            ticker=request.ticker,
        )
        memory_context = self._hermes.memory.recall_company(
            company=request.company,
            ticker=request.ticker,
        )
        result = self._hermes.analysis.analyze_company(
            company=request.company,
            ticker=request.ticker,
            questions=request.questions,
            graph_context=graph_context,
            memory_context=memory_context,
        )

        evidence = []
        for payload in result.evidence_payloads:
            kind = str(payload.get("kind", "research_evidence"))
            evidence_id = self._hermes.evidence.record(kind=kind, payload=payload)
            evidence.append(
                EvidenceReference(
                    evidence_id=evidence_id,
                    kind=kind,
                    summary=f"Hermes evidence recorded for {request.company}",
                    metadata={"company": request.company, "ticker": request.ticker},
                )
            )

        return CompanyResearchReport(
            company=request.company,
            ticker=request.ticker,
            thesis=result.thesis,
            risks=result.risks,
            catalysts=result.catalysts,
            confidence=result.confidence,
            evidence=tuple(evidence),
        )
