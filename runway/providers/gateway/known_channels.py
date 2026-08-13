"""Known-good example channel configs — data, not code. This replaces the
retired ``runway.providers.laozhang`` bespoke-adapter module: LaoZhang is
now just a :class:`GatewayConfig` + :class:`ChannelConfig` instance running
through the generic :class:`~runway.providers.gateway.execution.GatewayExecutionAdapter`,
same as any other OpenAI-compatible channel would be.

Deliberately the *only* entry here. Per the multi-gateway commissioning
task, several other candidate names were surveyed and found to be
subscription-pooling/reverse-engineered-access relays (not licensed API
resale) — those are not represented anywhere in this codebase, not even as
discovery-only records. See docs/architecture for the reasoning.
"""

from __future__ import annotations

from runway.providers.gateway.models import (
    ChannelConfig, GatewayConfig, GatewayProtocol, RailProvenance, RailRestriction,
)
from runway.trust import TrustTier

LAOZHANG_PROVIDER_ID = "laozhang"
LAOZHANG_CHANNEL_ID = "laozhang-openai-compatible"

#: Deliberate, documented trust-tier recommendation (unchanged from the
#: single-provider commissioning phase): a multi-provider reseller/
#: aggregator, matching Tier C, not a first-party vendor (Tier A/B) and not
#: an undocumented relay (Tier D).
RECOMMENDED_TRUST_TIER = TrustTier.VETTED_AGGREGATOR


def laozhang_gateway_config(*, enabled: bool = False) -> GatewayConfig:
    return GatewayConfig(
        provider_id=LAOZHANG_PROVIDER_ID,
        display_name="LaoZhang API",
        trust_tier=RECOMMENDED_TRUST_TIER,
        enabled=enabled,
        provenance=RailProvenance(
            operator="laozhang.ai (third-party multi-model gateway/reseller)",
            official_vendor_relationship=False,
            vetting_notes="Public OpenAI-compatible docs at docs.laozhang.ai; not a first-party vendor.",
        ),
        pricing_source="docs.laozhang.ai (per-model reference tables)",
        health_source="none configured",
        balance_source="none configured",
    )


def laozhang_channel_config(*, credential_ref: str, enabled: bool = False) -> ChannelConfig:
    return ChannelConfig(
        channel_id=LAOZHANG_CHANNEL_ID,
        provider_id=LAOZHANG_PROVIDER_ID,
        protocol=GatewayProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://api2.laozhang.ai/v1",
        credential_ref=credential_ref,
        enabled=enabled,
        provenance=RailProvenance(operator="laozhang.ai", official_vendor_relationship=False),
        restrictions=(RailRestriction.THIRD_PARTY_RESOLD, RailRestriction.MODEL_SUBSTITUTION_POSSIBLE),
    )
