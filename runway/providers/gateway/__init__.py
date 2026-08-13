"""Configuration-driven multi-gateway exchange layer.

Generalizes the single-provider ExecutionPort work into: reusable protocol
adapters (:mod:`runway.providers.gateway.protocols`), a
provider/channel/protocol/provenance/restriction identity model
(:mod:`runway.providers.gateway.models`), a channel registry
(:mod:`runway.providers.gateway.registry`), an eligibility-before-scoring
pipeline that reuses ``runway.scoring.RouteScorer`` unchanged
(:mod:`runway.providers.gateway.eligibility`, :mod:`runway.providers.gateway.routing`),
and a strict YAML config loader (:mod:`runway.providers.gateway.config_loader`).

Import :mod:`runway.providers.gateway.protocols` once (e.g. at process
start, or let ``execution.py``'s importers do it) to register the concrete
protocol handlers.
"""

from __future__ import annotations
