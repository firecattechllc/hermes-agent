from __future__ import annotations

import socket
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from sigil.dossiers import (
    DossierInput,
    FinancialHistory,
    FinancialPeriodObservation,
    ResearchCompletenessStatus,
    ResearchConclusion,
    ResearchDossierPolicy,
    ResearchEntityIdentity,
    ResearchEvidenceClaim,
    ResearchEvidenceConflict,
    ResearchEvidenceGap,
    ResearchEvidenceReference,
    ResearchFreshnessStatus,
    ResearchSecurityIdentity,
    build_dossier,
    cagr,
    claim_to_evidence,
    compare_dossiers,
    evidence_to_claims,
    growth,
    inspect_conclusion,
    list_conflicts,
    list_gaps,
    list_questions,
    list_stale_evidence,
    ratio,
    subtract,
    verify_dossier_identity,
)
from sigil.integrations.providers import FinancialDataValidationError

NOW = datetime(2026, 7, 24, 14, tzinfo=UTC)
DIGEST = "1" * 64


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in Step 14 tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def entity(**changes: object) -> ResearchEntityIdentity:
    values: dict[str, object] = {
        "issuer_id": "issuer-1",
        "legal_name": "Example Incorporated",
        "normalized_name": "example incorporated",
        "cik": "0000000001",
    }
    values.update(changes)
    return ResearchEntityIdentity(**values)  # type: ignore[arg-type]


def security(**changes: object) -> ResearchSecurityIdentity:
    values: dict[str, object] = {
        "security_id": "security-1",
        "issuer_id": "issuer-1",
        "ticker": "EXM",
        "instrument_type": "EQUITY",
        "security_type": "COMMON_STOCK",
        "currency": "USD",
        "exchange": "XNYS",
        "cik": "0000000001",
    }
    values.update(changes)
    return ResearchSecurityIdentity(**values)  # type: ignore[arg-type]


def evidence(identity: str = "evidence-1", **changes: object) -> ResearchEvidenceReference:
    values: dict[str, object] = {
        "evidence_id": identity,
        "source_identity": "source-1",
        "source_type": "sec_filing",
        "source_record_id": identity,
        "source_digest": DIGEST,
        "source_timestamp": NOW - timedelta(days=1),
        "acquired_at": NOW,
        "entity_id": "issuer-1",
        "security_id": "security-1",
        "document_identity": "document-1",
        "filing_identity": "filing-1",
        "locator": "Item.1",
        "fact_digest": DIGEST,
    }
    values.update(changes)
    return ResearchEvidenceReference(**values)  # type: ignore[arg-type]


def claim(identity: str = "claim-1", **changes: object) -> ResearchEvidenceClaim:
    values: dict[str, object] = {
        "claim_id": identity,
        "claim_type": "business_profile",
        "subject": "issuer-1",
        "predicate": "business_description",
        "normalized_value": "Provides verified example services.",
        "classification": "source_fact",
        "evidence_references": (evidence(),),
    }
    values.update(changes)
    return ResearchEvidenceClaim(**values)  # type: ignore[arg-type]


def observation(
    identity: str,
    metric: str,
    value: str,
    year: int,
    **changes: object,
) -> FinancialPeriodObservation:
    values: dict[str, object] = {
        "observation_id": identity,
        "entity_id": "issuer-1",
        "metric": metric,
        "period_start": date(year, 1, 1),
        "period_end": date(year, 12, 31),
        "fiscal_year": year,
        "value": value,
        "units": "USD",
        "evidence_reference": evidence(f"evidence-{identity}"),
    }
    values.update(changes)
    return FinancialPeriodObservation(**values)  # type: ignore[arg-type]


def complete_input(**changes: object) -> DossierInput:
    claims = tuple(
        claim(
            f"claim-{section}",
            claim_type=section,
            predicate=f"{section}_fact",
            evidence_references=(evidence(f"evidence-{section}"),),
        )
        for section in (
            "identity",
            "business_profile",
            "financial_history",
            "filings",
            "risk_factors",
        )
    )
    history = FinancialHistory(
        (
            observation("revenue-2024", "revenue", "100", 2024),
            observation("revenue-2025", "revenue", "120", 2025),
        )
    )
    values: dict[str, object] = {
        "entity": entity(),
        "security": security(),
        "claims": claims,
        "section_claims": tuple((item.claim_type, (item.claim_id,)) for item in claims),
        "financial_history": history,
        "filing_count": 1,
    }
    values.update(changes)
    return DossierInput(**values)  # type: ignore[arg-type]


def dossier(**changes: object):
    return build_dossier(
        complete_input(**changes), ResearchDossierPolicy(), constructed_at=NOW
    )


def test_valid_policy_and_deterministic_identity() -> None:
    assert ResearchDossierPolicy().policy_identity == ResearchDossierPolicy().policy_identity


@pytest.mark.parametrize(
    "changes",
    (
        {"maximum_filing_age": timedelta(days=-1)},
        {"supported_currencies": ("EUR",)},
        {"supported_instruments": ("OPTION",)},
        {"supported_filing_types": ("S-1",)},
        {"required_sections": ("identity", "identity")},
        {"required_sections": ("unknown",)},
        {
            "required_sections": ("identity",),
            "optional_sections": ("identity",),
        },
        {
            "minimum_evidence_per_required_section": 2,
            "maximum_evidence_per_section": 1,
        },
        {"maximum_total_evidence": 100_001},
        {"version": "authorization"},
        {"materiality_thresholds": (("revenue", "not-decimal"),)},
    ),
)
def test_invalid_policy_contracts(changes: dict[str, object]) -> None:
    with pytest.raises(FinancialDataValidationError):
        ResearchDossierPolicy(**changes)  # type: ignore[arg-type]


def test_entity_and_security_identity_contracts() -> None:
    assert entity().identity == entity().identity
    assert security().identity == security().identity
    with pytest.raises(FinancialDataValidationError, match="ambiguous"):
        entity(resolved=False)
    with pytest.raises(FinancialDataValidationError, match="CIK"):
        entity(cik="1")
    with pytest.raises(FinancialDataValidationError, match="instrument"):
        security(instrument_type="OPTION")
    with pytest.raises(FinancialDataValidationError, match="security type"):
        security(security_type="PREFERRED")


@pytest.mark.parametrize(
    "changes,match",
    (
        ({"source_digest": "bad"}, "digest"),
        ({"locator": None}, "locator"),
        ({"verification_status": "unverified"}, "verified"),
        ({"source_timestamp": NOW + timedelta(seconds=1)}, "after acquisition"),
        ({"source_type": "web_browse"}, "source type"),
        ({"excerpt": "x" * 501}, "excerpt"),
        ({"truncated": True}, "truncated"),
    ),
)
def test_evidence_rejections(changes: dict[str, object], match: str) -> None:
    with pytest.raises(FinancialDataValidationError, match=match):
        evidence(**changes)


def test_source_and_derived_claim_governance_and_identities() -> None:
    source = claim()
    derived = claim(
        "derived-1",
        classification="derived",
        evidence_references=(),
        source_claim_ids=(source.claim_id,),
        formula="identity",
    )
    assert source.claim_identity == claim().claim_identity
    assert derived.source_claim_ids == ("claim-1",)
    assert replace(source, normalized_value="changed", claim_identity="").claim_identity != (
        source.claim_identity
    )
    with pytest.raises(FinancialDataValidationError, match="requires evidence"):
        claim(evidence_references=())
    with pytest.raises(FinancialDataValidationError, match="source claims"):
        claim(classification="derived", evidence_references=())


def test_conflict_gap_and_question_preservation() -> None:
    conflict = ResearchEvidenceConflict(
        "conflict-1",
        "revenue",
        "financial_value",
        (evidence("evidence-a"), evidence("evidence-b")),
        ("100", "101"),
        "material",
    )
    gap = ResearchEvidenceGap(
        "gap-1", "financial_history", "missing_financial_period", "material"
    )
    result = dossier(conflicts=(conflict,), gaps=(gap,))
    assert list_conflicts(result) == (conflict,)
    assert list_gaps(result) == (gap,)
    assert len(list_questions(result)) == 2
    assert result.completeness is ResearchCompletenessStatus.PARTIAL
    assert not result.high_confidence_eligible


def test_resolved_conflict_requires_governed_selection() -> None:
    refs = (evidence("evidence-a"), evidence("evidence-b"))
    with pytest.raises(FinancialDataValidationError, match="governed selection"):
        ResearchEvidenceConflict(
            "conflict-1",
            "revenue",
            "financial_value",
            refs,
            ("100", "101"),
            "material",
            resolution_status="resolved",
        )
    resolved = ResearchEvidenceConflict(
        "conflict-1",
        "revenue",
        "financial_value",
        refs,
        ("100", "101"),
        "material",
        resolution_status="resolved",
        selected_value="101",
        resolution_reason="verified amendment",
        resolver_identity="policy-1",
    )
    assert resolved.selected_value == "101"


@pytest.mark.parametrize(
    "changes,match",
    (
        ({"period_kind": "quarterly"}, "fiscal quarter"),
        ({"period_kind": "annual", "fiscal_quarter": 1}, "annual"),
        ({"currency": "EUR"}, "currency"),
        ({"balance_type": "point"}, "balance type"),
        ({"value": 1.5}, "exact decimal"),
    ),
)
def test_financial_observation_validation(changes: dict[str, object], match: str) -> None:
    with pytest.raises(FinancialDataValidationError, match=match):
        values = {
            "observation_id": "revenue-2025",
            "entity_id": "issuer-1",
            "metric": "revenue",
            "period_start": date(2025, 1, 1),
            "period_end": date(2025, 12, 31),
            "fiscal_year": 2025,
            "value": "100",
            "units": "USD",
            "evidence_reference": evidence("evidence-revenue-2025"),
        }
        values.update(changes)
        FinancialPeriodObservation(**values)  # type: ignore[arg-type]


def test_cross_issuer_and_duplicate_financial_fact_rejected() -> None:
    with pytest.raises(FinancialDataValidationError, match="cross-issuer"):
        observation(
            "revenue-2025",
            "revenue",
            "100",
            2025,
            evidence_reference=evidence(entity_id="issuer-2"),
        )
    one = observation("one", "revenue", "100", 2025)
    two = observation("two", "revenue", "101", 2025)
    with pytest.raises(FinancialDataValidationError, match="duplicate financial"):
        FinancialHistory((one, two))


def test_exact_financial_growth_cagr_margins_cash_and_balance_sheet() -> None:
    revenue_2024 = observation("rev-24", "revenue", "100", 2024)
    revenue_2025 = observation("rev-25", "revenue", "125", 2025)
    gross = observation("gross-25", "gross_profit", "50", 2025)
    capex = observation("capex-25", "capital_expenditures", "20", 2025)
    ocf = observation("ocf-25", "operating_cash_flow", "70", 2025)
    debt = observation("debt-25", "total_debt", "40", 2025)
    cash = observation("cash-25", "cash", "60", 2025)
    assert growth(revenue_2025, revenue_2024).value == "0.25"
    assert cagr((revenue_2024, revenue_2025)).value == "0.25"
    assert ratio("gross_margin", gross, revenue_2025, "gross/revenue").value == "0.4"
    assert subtract("free_cash_flow", ocf, capex, "ocf-capex").value == "50"
    assert subtract("net_cash", cash, debt, "cash-debt").value == "20"


def test_invalid_denominator_and_insufficient_cagr_are_structured() -> None:
    zero = observation("zero", "revenue", "0", 2025)
    gross = observation("gross", "gross_profit", "5", 2025)
    assert ratio("gross_margin", gross, zero, "gross/revenue").value is None
    assert cagr((zero,)).value is None


@pytest.mark.parametrize(
    "changes,match",
    (
        ({"period_kind": "quarterly", "fiscal_quarter": 4}, "period mismatch"),
        ({"currency": None}, "currencies"),
        ({"units": "shares"}, "units"),
        ({"balance_type": "instant"}, "instant/duration"),
    ),
)
def test_incompatible_financial_derivations(
    changes: dict[str, object], match: str
) -> None:
    current = observation("current", "revenue", "100", 2025)
    prior = observation("prior", "revenue", "90", 2024, **changes)
    with pytest.raises(FinancialDataValidationError, match=match):
        growth(current, prior)


def test_complete_dossier_is_stable_auditable_and_read_only() -> None:
    result = dossier()
    assert result.completeness is ResearchCompletenessStatus.COMPLETE
    assert result.high_confidence_eligible
    assert verify_dossier_identity(result)
    assert dossier().dossier_identity == result.dossier_identity
    assert claim_to_evidence(result, "claim-identity")
    assert evidence_to_claims(result, "evidence-identity")[0].claim_id == "claim-identity"
    assert list_stale_evidence(result) == ()


def test_dossier_hash_changes_for_evidence_policy_financials_and_completeness() -> None:
    original = dossier()
    changed_claim = claim(
        "claim-identity",
        claim_type="identity",
        predicate="identity_fact",
        normalized_value="changed",
        evidence_references=(evidence("evidence-identity"),),
    )
    changed_claims = tuple(
        changed_claim if item.claim_id == "claim-identity" else item
        for item in complete_input().claims
    )
    assert dossier(claims=changed_claims).dossier_identity != original.dossier_identity
    other_policy = ResearchDossierPolicy(maximum_sentiment_age=timedelta(days=29))
    assert (
        build_dossier(complete_input(), other_policy, constructed_at=NOW).dossier_identity
        != original.dossier_identity
    )
    altered_history = FinancialHistory(
        (
            observation("revenue-2024", "revenue", "100", 2024),
            observation("revenue-2025", "revenue", "121", 2025),
        )
    )
    assert dossier(financial_history=altered_history).dossier_identity != original.dossier_identity
    assert dossier(filing_count=0).dossier_identity != original.dossier_identity


def test_cross_issuer_and_security_injection_rejected_by_engine() -> None:
    wrong_entity_claim = claim(
        "wrong",
        claim_type="identity",
        evidence_references=(evidence("wrong", entity_id="issuer-2"),),
    )
    with pytest.raises(FinancialDataValidationError, match="cross-issuer"):
        dossier(
            claims=complete_input().claims + (wrong_entity_claim,),
            section_claims=complete_input().section_claims
            + (("management", ("wrong",)),),
        )
    with pytest.raises(FinancialDataValidationError, match="issuer/security"):
        dossier(security=security(issuer_id="issuer-2"))


def test_future_evidence_beyond_policy_tolerance_is_rejected() -> None:
    future = claim(
        "claim-identity",
        claim_type="identity",
        predicate="identity_fact",
        evidence_references=(
            evidence(
                "evidence-identity",
                source_timestamp=NOW + timedelta(minutes=6),
                acquired_at=NOW + timedelta(minutes=6),
            ),
        ),
    )
    claims = tuple(
        future if item.claim_id == future.claim_id else item for item in complete_input().claims
    )
    with pytest.raises(FinancialDataValidationError, match="future evidence"):
        dossier(claims=claims)


def test_stale_and_truncated_material_evidence_fail_closed() -> None:
    stale_claim = claim(
        "claim-identity",
        claim_type="identity",
        predicate="identity_fact",
        evidence_references=(evidence("evidence-identity"),),
        freshness=ResearchFreshnessStatus.STALE,
    )
    claims = tuple(
        stale_claim if item.claim_id == stale_claim.claim_id else item
        for item in complete_input().claims
    )
    gap = ResearchEvidenceGap(
        "stale-filing",
        "filings",
        "stale_filing",
        "material",
        stale=True,
    )
    result = dossier(claims=claims, gaps=(gap,))
    assert list_stale_evidence(result)
    assert not result.high_confidence_eligible
    truncated = replace(gap, gap_id="truncated", stale=False, truncated=True, gap_identity="")
    assert not dossier(gaps=(truncated,)).high_confidence_eligible


def test_conclusion_contract_and_provenance() -> None:
    conclusion = ResearchConclusion(
        "conclusion-1",
        "financial_history",
        "improving_trend",
        "Reported revenue increased under the stated comparison rule.",
        ("claim-financial_history",),
        NOW,
        rule_identity="period_over_period_growth-v1",
    )
    result = build_dossier(
        complete_input(),
        ResearchDossierPolicy(),
        constructed_at=NOW,
        conclusions=(conclusion,),
    )
    inspected = inspect_conclusion(result, "conclusion-1")
    assert inspected is not None
    assert inspected[1][0].claim_id == "claim-financial_history"
    with pytest.raises(FinancialDataValidationError, match="supporting"):
        replace(conclusion, supporting_claim_ids=(), conclusion_identity="")


@pytest.mark.parametrize(
    "statement",
    (
        "Buy the shares.",
        "Sell the security.",
        "Hold this position.",
        "Target price is 20.",
        "This outcome is guaranteed.",
        "Allocate ten percent.",
    ),
)
def test_recommendation_language_is_prohibited(statement: str) -> None:
    with pytest.raises(FinancialDataValidationError, match="recommendation"):
        ResearchConclusion(
            "conclusion-1",
            "financial_history",
            "observed_strength",
            statement,
            ("claim-1",),
            NOW,
        )


def test_dossier_comparison_claim_gap_conflict_and_completeness_changes() -> None:
    old = dossier(
        gaps=(
            ResearchEvidenceGap(
                "gap-1", "filings", "missing_required_filing", "material"
            ),
        ),
        filing_count=0,
    )
    added = claim(
        "claim-management",
        claim_type="management",
        predicate="executive_role",
        evidence_references=(evidence("evidence-management"),),
    )
    new_input = complete_input(
        claims=complete_input().claims + (added,),
        section_claims=complete_input().section_claims
        + (("management", ("claim-management",)),),
    )
    new = build_dossier(new_input, ResearchDossierPolicy(), constructed_at=NOW)
    comparison = compare_dossiers(old, new)
    assert comparison.added_claims == ("claim-management",)
    assert comparison.completeness_change == "improved"
    assert comparison.comparison_identity == compare_dossiers(old, new).comparison_identity
    assert compare_dossiers(new, new).completeness_change == "unchanged"


def test_comparison_rejects_different_entities_and_securities() -> None:
    original = dossier()
    issuer_only = replace(original, security=None, dossier_identity="")
    alternate_entity = replace(
        issuer_only,
        entity=entity(issuer_id="issuer-2", cik="0000000002"),
        dossier_identity="",
    )
    with pytest.raises(FinancialDataValidationError, match="same entity"):
        compare_dossiers(issuer_only, alternate_entity)
    alternate = replace(
        original,
        security=security(security_id="security-2"),
        dossier_identity="",
    )
    with pytest.raises(FinancialDataValidationError, match="same security"):
        compare_dossiers(original, alternate)


def test_no_trade_or_mutation_capability_exposed() -> None:
    import sigil.dossiers as public

    forbidden = {
        "approve_trade",
        "authorize_trade",
        "execute_trade",
        "place_order",
        "append_journal",
        "write_ledger",
        "mutate_risk_report",
        "write_knowledge_graph",
    }
    assert forbidden.isdisjoint(dir(public))
