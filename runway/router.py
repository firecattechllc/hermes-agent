"""Top-level Runway entry point: composes registry + scoring + budget +
health + telemetry behind the ``runway_enabled`` flag (spec section 21).

This is the only class most callers should need. It stays deliberately thin
— all the actual policy lives in :mod:`runway.scoring`, :mod:`runway.budget`,
and :mod:`runway.trust`; this module just wires them together and makes sure
nothing runs while Runway is disabled.
"""

from __future__ import annotations

from typing import Optional

from runway.budget import BudgetLedger
from runway.flags import RunwayFeatureFlags
from runway.health import HealthProbe
from runway.identifiers import digest
from runway.outcomes import HistoricalPerformanceStore
from runway.registry import Registry
from runway.scoring import RouteDecision, RouteOutcome, RouteRequest, RouteScorer, RoutingWeights
from runway.telemetry import RunwayEvent, RunwayEventType, RunwayTelemetryLog


class RunwayDisabled(Exception):
    pass


class RunwayRouter:
    def __init__(
        self,
        registry: Registry,
        *,
        flags: RunwayFeatureFlags,
        health_probe: HealthProbe,
        budget_ledger: BudgetLedger,
        performance_store: HistoricalPerformanceStore,
        weights: Optional[RoutingWeights] = None,
        telemetry: Optional[RunwayTelemetryLog] = None,
    ) -> None:
        self._flags = flags
        self._scorer = RouteScorer(
            registry, weights=weights, health_probe=health_probe,
            budget_ledger=budget_ledger, performance_store=performance_store,
        )
        self._telemetry = telemetry

    def route(self, request: RouteRequest, *, timestamp: int) -> RouteDecision:
        if not self._flags.runway_enabled:
            raise RunwayDisabled("runway_enabled flag is off")

        decision = self._scorer.route(request, timestamp=timestamp)
        self._emit(decision, timestamp=timestamp)
        return decision

    def _emit(self, decision: RouteDecision, *, timestamp: int) -> None:
        if self._telemetry is None:
            return
        self._telemetry.append(RunwayEvent(
            event_id=f"evt_{digest((decision.decision_id, 'evaluated'))[:24]}",
            event_type=RunwayEventType.ROUTE_CANDIDATES_EVALUATED,
            timestamp=timestamp, correlation_id=decision.request_id,
            payload={"candidate_count": len(decision.candidates), "decision_id": decision.decision_id},
        ))
        if decision.outcome == RouteOutcome.ROUTED:
            self._telemetry.append(RunwayEvent(
                event_id=f"evt_{digest((decision.decision_id, 'selected'))[:24]}",
                event_type=RunwayEventType.ROUTE_SELECTED,
                timestamp=timestamp, correlation_id=decision.request_id,
                payload={
                    "decision_id": decision.decision_id,
                    "provider_id": decision.selected_provider_id,
                    "model_key": decision.selected_model_key,
                    "estimated_cost_micros": decision.estimated_cost_micros,
                    "expected_cost_to_success_micros": decision.expected_cost_to_success_micros,
                },
            ))
        else:
            self._telemetry.append(RunwayEvent(
                event_id=f"evt_{digest((decision.decision_id, 'rejected'))[:24]}",
                event_type=RunwayEventType.ROUTE_REJECTED_BY_POLICY,
                timestamp=timestamp, correlation_id=decision.request_id,
                payload={"decision_id": decision.decision_id, "candidate_count": len(decision.candidates)},
            ))
