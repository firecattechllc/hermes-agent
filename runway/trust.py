"""Provider trust model (spec section 3).

Trust classes are data-driven: nothing in this module or anywhere else in
Runway hardcodes a real provider name into policy logic. A provider is
*assigned* a tier as a field on its :class:`~runway.registry.ProviderRecord`;
the tier then gates which :class:`~runway.classification.DataClassification`
values it may ever receive, via :data:`TIER_DATA_CLASSIFICATION_CEILING`.

Trust tier alone is necessary but never sufficient to route real traffic —
:mod:`runway.lifecycle` additionally requires ``APPROVED`` state. A
suspiciously cheap Tier D route cannot out-rank a Tier A route on price
alone; see :mod:`runway.scoring`.
"""

from __future__ import annotations

from enum import IntEnum

from runway.classification import DataClassification


class TrustTier(IntEnum):
    """Ordered worst-to-best. Default for anything discovered automatically
    is :attr:`EXPERIMENTAL_UNKNOWN` — see :mod:`runway.discovery`.
    """

    EXPERIMENTAL_UNKNOWN = 0   # Tier D: unknown relays, subscription bridges, unclear provenance
    VETTED_AGGREGATOR = 1      # Tier C: multi-provider gateways that passed qualification
    ESTABLISHED_CLOUD = 2      # Tier B: established hosted inference platforms
    DIRECT_OFFICIAL = 3        # Tier A: official model-vendor APIs, established direct relationships


#: The most sensitive data classification each tier may ever receive.
#: Data-driven, overridable by policy construction (see
#: :func:`data_classification_allowed`) rather than scattered `if` checks.
TIER_DATA_CLASSIFICATION_CEILING: dict[TrustTier, DataClassification] = {
    TrustTier.EXPERIMENTAL_UNKNOWN: DataClassification.SYNTHETIC,
    TrustTier.VETTED_AGGREGATOR: DataClassification.INTERNAL,
    TrustTier.ESTABLISHED_CLOUD: DataClassification.PROPRIETARY,
    TrustTier.DIRECT_OFFICIAL: DataClassification.RESTRICTED,
}


def data_classification_allowed(
    tier: TrustTier,
    classification: DataClassification,
    *,
    ceilings: dict[TrustTier, DataClassification] | None = None,
) -> bool:
    """Fail-closed: unknown tiers/classes are never allowed implicitly because
    both are exhaustive IntEnums and ``ceilings`` must cover every tier.
    """
    table = ceilings if ceilings is not None else TIER_DATA_CLASSIFICATION_CEILING
    if tier not in table:
        return False
    return classification <= table[tier]
