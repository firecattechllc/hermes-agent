from __future__ import annotations

import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sigil.dossiers import (
    DossierInput,
    ResearchDossierPolicy,
    ResearchEntityIdentity,
    ResearchEvidenceClaim,
    ResearchEvidenceReference,
    ResearchSecurityIdentity,
    build_dossier,
)
from sigil.integrations.providers import FinancialDataValidationError
from sigil.theses import (
    CounterThesisPillar,
    InvestmentCounterThesis,
    InvestmentThesis,
    InvestmentThesisInput,
    InvestmentThesisPolicy,
    ThesisArgument,
    ThesisArgumentType,
    ThesisAssumption,
    ThesisCompletenessClassification,
    ThesisConstruction,
    ThesisFalsificationTest,
    ThesisInvalidationCondition,
    ThesisMonitoringIndicator,
    ThesisPillar,
    ThesisReadinessClassification,
    ThesisRisk,
    build_thesis_package,
    compare_thesis_packages,
    evaluate_falsification_test,
    evaluate_invalidation_condition,
)
from sigil.theses.audit import (
    argument_to_claims,
    claim_to_arguments,
    confidence_component_summary,
    list_falsification_tests,
    list_invalidation_conditions,
    list_monitoring_indicators,
    list_readiness_blockers,
    list_risks,
    list_unsupported_assumptions,
    regenerate_package,
    verify_package_identity,
)

NOW = datetime(2026, 7, 24, 14, tzinfo=UTC)
DIGEST = "1" * 64


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in Step 15 tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def evidence(evidence_id: str, source: str) -> ResearchEvidenceReference:
    return ResearchEvidenceReference(
        evidence_id,
        source,
        "sec_filing",
        evidence_id,
        DIGEST,
        NOW - timedelta(days=1),
        NOW,
        "issuer-1",
        "security-1",
        locator="Item.1",
        fact_digest=DIGEST,
    )


def claim(claim_id: str, section: str, source: str) -> ResearchEvidenceClaim:
    return ResearchEvidenceClaim(
        claim_id,
        section,
        "issuer-1",
        f"{section}_fact",
        f"Verified {section} evidence.",
        "source_fact",
        (evidence(f"evidence-{claim_id}", source),),
    )


@pytest.fixture
def dossier():
    claims = tuple(
        claim(f"claim-{section}", section, f"source-{index}")
        for index, section in enumerate(
            ("identity", "business_profile", "financial_history", "filings", "risk_factors")
        )
    )
    entity = ResearchEntityIdentity(
        "issuer-1", "Example Incorporated", "example incorporated", "0000000001"
    )
    security = ResearchSecurityIdentity(
        "security-1",
        "issuer-1",
        "EXM",
        "EQUITY",
        "COMMON_STOCK",
        "USD",
        cik="0000000001",
    )
    return build_dossier(
        DossierInput(
            entity,
            security,
            claims,
            tuple((item.claim_type, (item.claim_id,)) for item in claims),
            filing_count=1,
        ),
        ResearchDossierPolicy(minimum_financial_history_periods=0),
        constructed_at=NOW,
    )


def assumption(**changes: object) -> ThesisAssumption:
    values: dict[str, object] = {
        "assumption_id": "assumption-1",
        "statement": "Verified demand conditions continue through the review window.",
        "category": "operational",
        "materiality": "material",
        "supporting_claim_ids": ("claim-business_profile",),
        "verification_status": "verified",
    }
    values.update(changes)
    return ThesisAssumption(**values)  # type: ignore[arg-type]


def argument(dossier, argument_id: str, direction: str, claim_id: str, **changes: object):
    values: dict[str, object] = {
        "argument_id": argument_id,
        "argument_type": ThesisArgumentType.BUSINESS_QUALITY,
        "statement": f"Evidence supports a bounded interpretation for {argument_id}.",
        "subject_identity": dossier.security.identity,
        "direction": direction,
        "materiality": "material",
        "supporting_claim_ids": (claim_id,),
        "assumption_ids": ("assumption-1",),
        "causal_mechanism": "The observed operating fact may influence future operating results.",
        "timeframe": "Within twelve months.",
    }
    values.update(changes)
    return ThesisArgument(**values)


def invalidation(**changes: object) -> ThesisInvalidationCondition:
    values: dict[str, object] = {
        "condition_id": "invalidation-1",
        "related_pillar_ids": ("thesis-pillar-1",),
        "observable_condition": "Verified annual revenue is below the supplied threshold.",
        "evidence_type": "verified_financial_metric",
        "operator": "<",
        "threshold": "100",
        "time_window": "At the next annual filing.",
        "required_source": "A normalized Step 14 filing claim.",
    }
    values.update(changes)
    return ThesisInvalidationCondition(**values)  # type: ignore[arg-type]


def indicator() -> ThesisMonitoringIndicator:
    return ThesisMonitoringIndicator(
        "indicator-1",
        "Monitor verified annual revenue.",
        "financial_metric",
        "Caller supplies an immutable normalized dossier claim.",
        "Review after each annual filing.",
        "Stale after eighteen months.",
        "material",
        ("thesis-pillar-1",),
        ("assumption-1",),
        ("invalidation-1",),
    )


def falsification() -> ThesisFalsificationTest:
    return ThesisFalsificationTest(
        "falsification-1",
        "The observed operating condition continues.",
        ("annual-revenue",),
        "Compare exact decimal observation with the stated threshold.",
        "The observation meets the stated threshold.",
        "The observation does not meet the stated threshold.",
        "At the next annual filing.",
    )


def risk() -> ThesisRisk:
    return ThesisRisk(
        "risk-1",
        "operating",
        "Reported operating conditions may deteriorate.",
        "Lower demand could reduce reported revenue.",
        ("claim-risk_factors",),
        (),
        "possible",
        "high",
        "material",
        monitoring_indicator_ids=("indicator-1",),
        invalidation_condition_ids=("invalidation-1",),
    )


def construction(dossier, **changes: object) -> ThesisConstruction:
    thesis_argument = argument(
        dossier, "argument-thesis", "supports_thesis", "claim-business_profile"
    )
    counter_argument = argument(
        dossier, "argument-counter", "supports_counter_thesis", "claim-risk_factors"
    )
    thesis_pillar = ThesisPillar(
        "thesis-pillar-1",
        "Operating evidence",
        "Verified operating evidence supports the bounded hypothesis.",
        ("argument-thesis",),
        ("claim-business_profile",),
        (),
        ("assumption-1",),
        (),
        (),
        ("risk-1",),
        ("invalidation-1",),
        ("indicator-1",),
        "material",
        ThesisCompletenessClassification.COMPLETE,
        "high",
    )
    counter_pillar = CounterThesisPillar(
        "counter-pillar-1",
        "Independent failure mechanism",
        "Verified risk evidence supports an alternative operating outcome.",
        ("argument-counter",),
        ("claim-risk_factors",),
        (),
        ("assumption-1",),
        (),
        (),
        ("risk-1",),
        ("invalidation-1",),
        (),
        "material",
        ThesisCompletenessClassification.COMPLETE,
        "moderate",
        alternative_explanation="The positive evidence may reflect a temporary operating condition.",
        failure_mechanism="Demand deterioration may reverse the observed operating condition.",
    )
    values: dict[str, object] = {
        "investment_thesis": InvestmentThesis(
            "Verified evidence supports a falsifiable operating hypothesis.",
            ("thesis-pillar-1",),
            "The hypothesis is structured for governed review only.",
        ),
        "counter_thesis": InvestmentCounterThesis(
            "Verified risk evidence supports an independent alternative hypothesis.",
            ("counter-pillar-1",),
            "The alternative remains unresolved and requires monitoring.",
        ),
        "arguments": (thesis_argument, counter_argument),
        "thesis_pillars": (thesis_pillar,),
        "counter_thesis_pillars": (counter_pillar,),
        "assumptions": (assumption(),),
        "risks": (risk(),),
        "invalidation_conditions": (invalidation(),),
        "falsification_tests": (falsification(),),
        "monitoring_indicators": (indicator(),),
    }
    values.update(changes)
    return ThesisConstruction(**values)  # type: ignore[arg-type]


def thesis_input(dossier, **changes: object) -> InvestmentThesisInput:
    values: dict[str, object] = {
        "dossier_identity": dossier.dossier_identity,
        "issuer_id": dossier.entity.issuer_id,
        "security_id": dossier.security.security_id,
        "selected_claim_ids": tuple(item.claim_id for item in dossier.claims),
    }
    values.update(changes)
    return InvestmentThesisInput(**values)  # type: ignore[arg-type]


def package(dossier, **construction_changes: object):
    policy = InvestmentThesisPolicy()
    return build_thesis_package(
        dossier,
        thesis_input(dossier),
        policy,
        construction(dossier, **construction_changes),
        constructed_at=NOW,
        thesis_horizon="Twelve months from construction.",
    )


def test_valid_policy_and_deterministic_policy_identity() -> None:
    assert InvestmentThesisPolicy().policy_identity == InvestmentThesisPolicy().policy_identity


@pytest.mark.parametrize(
    "changes",
    (
        {"minimum_counter_thesis_pillars": 0},
        {"minimum_invalidation_conditions": 0},
        {"minimum_supporting_claims_per_pillar": 0},
        {"required_thesis_sections": ("hypothesis", "hypothesis")},
        {"required_counter_thesis_sections": ()},
        {"allowed_currencies": ("EUR",)},
        {"allowed_instruments": ("OPTION",)},
        {"maximum_pillars": -1},
        {"maximum_construction_duration": timedelta(0)},
        {"version": "api_key"},
    ),
)
def test_invalid_policy_contracts(changes: dict[str, object]) -> None:
    with pytest.raises(FinancialDataValidationError):
        InvestmentThesisPolicy(**changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes,match",
    (
        ({"dossier_identity": "2" * 64}, "dossier identity"),
        ({"issuer_id": "issuer-2"}, "issuer mismatch"),
        ({"security_id": "security-2"}, "security mismatch"),
        ({"selected_claim_ids": ("unknown",)}, "unknown claim"),
    ),
)
def test_input_binding_rejections(dossier, changes: dict[str, object], match: str) -> None:
    with pytest.raises(FinancialDataValidationError, match=match):
        build_thesis_package(
            dossier,
            thesis_input(dossier, **changes),
            InvestmentThesisPolicy(),
            construction(dossier),
            constructed_at=NOW,
            thesis_horizon="Twelve months.",
        )


def test_secret_bearing_input_rejected(dossier) -> None:
    with pytest.raises(FinancialDataValidationError, match="secret"):
        thesis_input(dossier, framing="api_key=forbidden")


def test_argument_requires_evidence_and_preserves_contradictions(dossier) -> None:
    with pytest.raises(FinancialDataValidationError, match="supporting claims"):
        argument(dossier, "empty", "supports_thesis", "claim-identity", supporting_claim_ids=())
    item = argument(
        dossier,
        "contradicted",
        "supports_thesis",
        "claim-business_profile",
        contradicting_claim_ids=("claim-risk_factors",),
    )
    assert item.contradicting_claim_ids == ("claim-risk_factors",)
    assert item.argument_digest != replace(item, contradicting_claim_ids=(), argument_digest="").argument_digest


@pytest.mark.parametrize(
    "statement",
    (
        "Buy the security.",
        "Sell the security.",
        "Hold the security.",
        "The target price is 100.",
        "Allocate ten percent.",
        "The fair value is 100.",
    ),
)
def test_prohibited_recommendation_language_rejected(dossier, statement: str) -> None:
    with pytest.raises(FinancialDataValidationError, match="recommendation"):
        argument(
            dossier,
            "forbidden",
            "supports_thesis",
            "claim-business_profile",
            statement=statement,
        )


def test_counter_thesis_mere_negation_rejected(dossier) -> None:
    base = construction(dossier).counter_thesis_pillars[0]
    with pytest.raises(FinancialDataValidationError, match="mere negation"):
        replace(base, proposition="Not the thesis proposition.", pillar_identity="")


def test_counter_thesis_must_use_independent_argument(dossier) -> None:
    base = construction(dossier)
    counter = replace(
        base.counter_thesis_pillars[0],
        argument_ids=("argument-thesis",),
        pillar_identity="",
    )
    with pytest.raises(FinancialDataValidationError, match="independently"):
        package(dossier, counter_thesis_pillars=(counter,))


def test_vague_invalidation_rejected() -> None:
    with pytest.raises(FinancialDataValidationError, match="vague"):
        invalidation(observable_condition="the company gets worse")


def test_invalidation_evaluation_missing_is_not_clear() -> None:
    unavailable = evaluate_invalidation_condition(invalidation(), None, evaluated_at=NOW)
    triggered = evaluate_invalidation_condition(invalidation(), "90", evaluated_at=NOW)
    clear = evaluate_invalidation_condition(invalidation(), "110", evaluated_at=NOW)
    assert unavailable.status == "unavailable"
    assert triggered.status == "triggered"
    assert clear.status == "clear"


def test_falsification_evaluation_states() -> None:
    assert evaluate_falsification_test(falsification(), falsified=False).status == "passed"
    assert evaluate_falsification_test(falsification(), falsified=True).status == "falsified"
    assert evaluate_falsification_test(falsification(), falsified=None).status == "unavailable"


def test_complete_package_is_ready_for_review_not_trade_approval(dossier) -> None:
    result = package(dossier)
    assert result.completeness == ThesisCompletenessClassification.COMPLETE
    assert result.readiness == ThesisReadinessClassification.READY_FOR_REVIEW
    assert "approval" not in result.readiness.value
    assert verify_package_identity(result)


@pytest.mark.parametrize(
    "changes,blocker",
    (
        ({"invalidation_conditions": ()}, "invalidation_conditions"),
        ({"falsification_tests": ()}, "falsification_tests"),
        (
            {
                "assumptions": (
                    assumption(
                        supporting_claim_ids=(),
                        verification_status="unsupported",
                    ),
                )
            },
            "unsupported_material_assumptions",
        ),
    ),
)
def test_required_governance_absence_blocks_readiness(
    dossier, changes: dict[str, object], blocker: str
) -> None:
    result = package(dossier, **changes)
    assert result.readiness == ThesisReadinessClassification.BLOCKED
    assert blocker in result.readiness_blockers


def test_stale_evidence_blocks_readiness(dossier) -> None:
    base = construction(dossier)
    stale = replace(base.arguments[0], stale=True, argument_digest="")
    result = package(dossier, arguments=(stale, base.arguments[1]))
    assert result.readiness == ThesisReadinessClassification.BLOCKED
    assert "stale_evidence" in result.readiness_blockers


def test_deterministic_order_hash_and_regeneration(dossier) -> None:
    first = package(dossier)
    second = package(dossier)
    assert first.package_identity == second.package_identity
    rebuilt = regenerate_package(
        dossier,
        thesis_input(dossier),
        InvestmentThesisPolicy(),
        construction(dossier),
        first,
    )
    assert rebuilt == first


def test_package_hash_changes_for_material_changes(dossier) -> None:
    first = package(dossier)
    base = construction(dossier)
    changed_argument = replace(
        base.arguments[0],
        statement="Evidence supports a changed bounded operating interpretation.",
        argument_digest="",
    )
    second = package(dossier, arguments=(changed_argument, base.arguments[1]))
    assert first.package_identity != second.package_identity


def test_audit_lookups_are_read_only(dossier) -> None:
    result = package(dossier)
    before = result.package_identity
    assert argument_to_claims(result, "argument-thesis") == ("claim-business_profile",)
    assert claim_to_arguments(result, "claim-risk_factors")[0].argument_id == "argument-counter"
    assert list_unsupported_assumptions(result) == ()
    assert len(list_risks(result)) == 1
    assert len(list_invalidation_conditions(result)) == 1
    assert len(list_falsification_tests(result)) == 1
    assert len(list_monitoring_indicators(result)) == 1
    assert list_readiness_blockers(result) == ()
    assert dict(confidence_component_summary(result))["falsifiable"] == "true"
    assert result.package_identity == before


def test_compare_identical_and_changed_packages(dossier) -> None:
    first = package(dossier)
    assert compare_thesis_packages(first, first).changes == ()
    base = construction(dossier)
    changed = replace(
        base.arguments[0],
        statement="Evidence supports another bounded interpretation.",
        argument_digest="",
    )
    second = package(dossier, arguments=(changed, base.arguments[1]))
    comparison = compare_thesis_packages(first, second)
    assert ("changed_arguments", ("argument-thesis",)) in comparison.changes


def test_comparison_rejects_different_entity(dossier) -> None:
    first = package(dossier)
    other = replace(first, issuer_id="issuer-2", package_identity="")
    with pytest.raises(FinancialDataValidationError, match="same resolved entity"):
        compare_thesis_packages(first, other)
