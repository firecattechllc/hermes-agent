"""Eligibility pipeline (spec section 7): eliminate ineligible rails
*before* economic scoring. Deterministic, ordered, explainable — every
rejection carries a specific reason, same philosophy as
``runway.scoring.RouteScorer``.

Hard restrictions always win over price: a candidate this module rejects
never reaches :mod:`runway.scoring`, so a cheap-but-restricted rail can
never economically outrank a correctly-scoped one (spec: "Do NOT allow
price to override a hard restriction").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from runway.budget import BudgetLedger
from runway.capabilities import Capability
from runway.classification import DataClassification
from runway.flags import RunwayFeatureFlags
from runway.health import HealthProbe, HealthStatus
from runway.providers.gateway.models import BalanceObservationState, GatewayConfig, RailRestriction
from runway.providers.gateway.registry import ChannelRegistry
from runway.trust import data_classification_allowed

_CAPABILITY_BLOCKED_BY_RESTRICTION = {
    RailRestriction.NO_IMAGES: (Capability.VISION, Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING),
    RailRestriction.NO_TOOLS: (Capability.TOOL_CALLING, Capability.PARALLEL_TOOL_CALLING),
}


@dataclass(frozen=True)
class EligibilityRequest:
    model_key: str
    data_classification: DataClassification
    required_capabilities: Tuple[Capability, ...] = ()
    client_class: Optional[str] = None
    estimated_cost_micros: int = 0
    timestamp: int = 0
    #: Policy switch for the "quota/balance sufficient or unknown" step
    #: (spec section 6/7) — conservative default matches the rest of Runway
    #: ("budget policy must default conservatively").
    require_known_balance: bool = True


@dataclass(frozen=True)
class EligibilityResult:
    channel_id: Optional[str]
    eligible: bool
    reasons: Tuple[str, ...] = ()


def check_channel_eligibility(
    channels: ChannelRegistry,
    request: EligibilityRequest,
    *,
    flags: RunwayFeatureFlags,
    health_probe: HealthProbe,
    budget_ledger: Optional[BudgetLedger] = None,
) -> EligibilityResult:
    channel = channels.channel_for_model(request.model_key)
    if channel is None:
        return EligibilityResult(channel_id=None, eligible=False, reasons=("no_channel_for_model",))

    reasons: list[str] = []
    provider: GatewayConfig = channels.provider(channel.provider_id)

    # 1-2. provider enabled, channel enabled
    if not provider.enabled:
        reasons.append("provider_disabled")
    if not channel.enabled:
        reasons.append("channel_disabled")
    # 3. explicitly authorized
    if not channels.is_authorized(channel.channel_id):
        reasons.append("channel_not_authorized")
    # 4. external execution global gate
    if not flags.external_execution_enabled:
        reasons.append("external_execution_not_permitted")
    # 6. client restriction
    if channel.allowed_client_classes and request.client_class not in channel.allowed_client_classes:
        reasons.append("client_class_restricted")
    # 7. requested capabilities vs restrictions
    for restriction, blocked_caps in _CAPABILITY_BLOCKED_BY_RESTRICTION.items():
        if restriction in channel.restrictions and any(c in blocked_caps for c in request.required_capabilities):
            reasons.append(f"restriction_{restriction.value}")
    # 8. provenance/trust policy vs data classification
    if not data_classification_allowed(provider.trust_tier, request.data_classification):
        reasons.append("data_classification_not_eligible_for_trust_tier")
    # 9. rail health
    health = health_probe.provider_health(channel.channel_id).best_endpoint_status()
    if health == HealthStatus.UNHEALTHY:
        reasons.append("channel_unhealthy")
    # 10. quota/balance
    balance = channels.latest_balance(channel.channel_id)
    if balance is None:
        if request.require_known_balance:
            reasons.append("balance_unknown_and_required")
    elif balance.state == BalanceObservationState.UNAVAILABLE:
        if request.require_known_balance:
            reasons.append("balance_unavailable_and_required")
    elif balance.state == BalanceObservationState.STALE:
        if request.require_known_balance:
            reasons.append("balance_stale_and_required")
    elif balance.state == BalanceObservationState.KNOWN:
        if balance.remaining_micros is not None and balance.remaining_micros <= 0:
            reasons.append("balance_exhausted")
    # 12. request budget cap
    if budget_ledger is not None and request.estimated_cost_micros > 0:
        room = budget_ledger.headroom(
            provider_id=channel.provider_id, model_key=request.model_key, timestamp=request.timestamp,
        )
        if request.estimated_cost_micros > room.binding_remaining_micros:
            reasons.append("budget_exceeded")

    return EligibilityResult(channel_id=channel.channel_id, eligible=not reasons, reasons=tuple(sorted(reasons)))
