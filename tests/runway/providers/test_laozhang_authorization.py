"""Phase F groundwork: LaoZhang's trust-tier recommendation and proof that
authorization stays fail-closed until real qualification evidence exists.

No live call happens anywhere in this file. This documents/tests the state
LaoZhang is actually left in by this commit: registered as a candidate with
a deliberately chosen trust tier, still `discovered`, still unauthorized.
"""

from __future__ import annotations

import pytest

from runway.classification import DataClassification
from runway.lifecycle import LifecycleState
from runway.providers.laozhang import PROVIDER_ID, RECOMMENDED_TRUST_TIER
from runway.qualification import QualificationError, QualificationHistoryStore, QualificationService
from runway.registry import ProviderRecord, Registry
from runway.trust import TrustTier, data_classification_allowed


def test_recommended_trust_tier_is_vetted_aggregator_not_direct_official():
    # LaoZhang is a reseller/aggregator, not a first-party model vendor —
    # it must never default to the top trust tier.
    assert RECOMMENDED_TRUST_TIER == TrustTier.VETTED_AGGREGATOR
    assert RECOMMENDED_TRUST_TIER < TrustTier.DIRECT_OFFICIAL
    assert RECOMMENDED_TRUST_TIER < TrustTier.ESTABLISHED_CLOUD


def test_recommended_trust_tier_data_classification_ceiling():
    assert data_classification_allowed(RECOMMENDED_TRUST_TIER, DataClassification.INTERNAL) is True
    assert data_classification_allowed(RECOMMENDED_TRUST_TIER, DataClassification.PROPRIETARY) is False
    assert data_classification_allowed(RECOMMENDED_TRUST_TIER, DataClassification.RESTRICTED) is False


def test_laozhang_candidate_registration_starts_discovered_not_authorized():
    provider = ProviderRecord(
        provider_id=PROVIDER_ID, display_name="LaoZhang API",
        trust_tier=RECOMMENDED_TRUST_TIER, lifecycle_state=LifecycleState.DISCOVERED,
        credential_ref="env:LAOZHANG_API_KEY",
    )
    assert provider.lifecycle_state == LifecycleState.DISCOVERED
    registry = Registry(providers=(provider,))
    assert registry.provider(PROVIDER_ID).lifecycle_state != LifecycleState.APPROVED


def test_authorize_denied_before_real_qualification_evidence_exists(tmp_path):
    from runway.flags import RunwayFeatureFlags

    history = QualificationHistoryStore.open(path=tmp_path / "qual.db")
    service = QualificationService(history, flags=RunwayFeatureFlags(synthetic_qualification_enabled=True))

    provider = ProviderRecord(
        provider_id=PROVIDER_ID, display_name="LaoZhang API",
        trust_tier=RECOMMENDED_TRUST_TIER, lifecycle_state=LifecycleState.DISCOVERED,
        credential_ref="env:LAOZHANG_API_KEY",
    )
    registry = Registry(providers=(provider,))

    # No canary — synthetic or live — has run yet. authorize() must refuse.
    with pytest.raises(QualificationError):
        service.authorize(registry, PROVIDER_ID, now=0, authorized_by="ops")

    assert registry.provider(PROVIDER_ID).lifecycle_state == LifecycleState.DISCOVERED
