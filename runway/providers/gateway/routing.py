"""Composes channel eligibility with the existing, unmodified
``runway.scoring.RouteScorer`` (spec section 8: "Reuse existing Runway
scoring as much as possible. Do not rewrite the economic algorithm merely
because we discovered many gateways.").

Order: eliminate channel-ineligible rails first (section 7), then hand the
survivors to RouteScorer exactly as the foundation already does. RouteScorer
itself is untouched — this module only decides which candidates it ever
sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from runway.budget import BudgetLedger
from runway.flags import RunwayFeatureFlags
from runway.health import HealthProbe
from runway.outcomes import HistoricalPerformanceStore
from runway.providers.gateway.eligibility import EligibilityRequest, EligibilityResult, check_channel_eligibility
from runway.providers.gateway.registry import ChannelRegistry
from runway.registry import Registry
from runway.scoring import RouteDecision, RouteRequest, RouteScorer, RoutingWeights


@dataclass(frozen=True)
class GatewayRouteDecision:
    route_decision: RouteDecision
    #: Channel-eligibility results for every candidate model, including
    #: ones RouteScorer never saw because they were filtered out first —
    #: kept separate from RouteDecision (which stays byte-for-byte the
    #: existing, unmodified schema) so a rail rejected for a channel reason
    #: ("client_class_restricted", "balance_exhausted", ...) is still
    #: explainable.
    channel_eligibility: Tuple[EligibilityResult, ...]


class GatewayRouter:
    def __init__(
        self,
        registry: Registry,
        channels: ChannelRegistry,
        *,
        flags: RunwayFeatureFlags,
        health_probe: HealthProbe,
        budget_ledger: BudgetLedger,
        performance_store: HistoricalPerformanceStore,
        weights: Optional[RoutingWeights] = None,
        require_known_balance: bool = True,
    ) -> None:
        self._registry = registry
        self._channels = channels
        self._flags = flags
        self._health = health_probe
        self._budget = budget_ledger
        self._performance = performance_store
        self._weights = weights
        self._require_known_balance = require_known_balance

    def route(self, request: RouteRequest, *, timestamp: int) -> GatewayRouteDecision:
        eligibility_results = []
        eligible_model_keys = set()
        for model in self._registry.models:
            if request.task_type not in model.task_types:
                continue  # not a candidate for this request at all; RouteScorer will say so
            result = check_channel_eligibility(
                self._channels,
                EligibilityRequest(
                    model_key=model.model_key, data_classification=request.data_classification,
                    required_capabilities=request.required_capabilities, timestamp=timestamp,
                    require_known_balance=self._require_known_balance,
                ),
                flags=self._flags, health_probe=self._health, budget_ledger=self._budget,
            )
            eligibility_results.append(result)
            if result.eligible:
                eligible_model_keys.add(model.model_key)

        filtered_models = tuple(m for m in self._registry.models if m.model_key in eligible_model_keys)
        filtered_registry = Registry(providers=self._registry.providers, models=filtered_models)
        scorer = RouteScorer(
            filtered_registry, weights=self._weights, health_probe=self._health,
            budget_ledger=self._budget, performance_store=self._performance,
        )
        decision = scorer.route(request, timestamp=timestamp)
        return GatewayRouteDecision(route_decision=decision, channel_eligibility=tuple(eligibility_results))
