import pytest

from sigil.domain import CompanyResearchRequest
from sigil.integrations.hermes import HermesClient, InMemoryHermesAdapter
from sigil.workflows import ResearchCompanyWorkflow


def build_workflow():
    adapter = InMemoryHermesAdapter()
    client = HermesClient(
        graph=adapter,
        memory=adapter,
        analysis=adapter,
        evidence=adapter,
    )
    return ResearchCompanyWorkflow(client), adapter


def test_research_company_vertical_slice_records_evidence():
    workflow, adapter = build_workflow()

    report = workflow.run(
        CompanyResearchRequest(
            company="NVIDIA Corporation",
            ticker="nvda",
            questions=("What are the primary catalysts?",),
        )
    )

    assert report.company == "NVIDIA Corporation"
    assert report.ticker == "NVDA"
    assert report.confidence == "foundation-only"
    assert len(report.evidence) == 1
    assert adapter.recorded[0]["kind"] == "workflow_execution"


def test_research_company_rejects_blank_company():
    workflow, _ = build_workflow()

    with pytest.raises(ValueError, match="company is required"):
        workflow.run(CompanyResearchRequest(company="   "))
