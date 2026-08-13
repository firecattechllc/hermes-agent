"""Project Runway: AI compute procurement, qualification, routing-intelligence,
cost-control, and provider-governance layer for Hermes.

Runway is a *decide* layer only. It never executes a model call, never holds
credentials, and never talks to a real external provider. In this build it is
disabled by default (see :mod:`runway.flags`) and every provider it can route
to is a fixture or a deterministic in-process fake (see :mod:`runway.testing`).

See ``docs/architecture/PROJECT_RUNWAY_FOUNDATION.md`` for the full design.
"""

from __future__ import annotations

RUNWAY_SCHEMA_VERSION = 1
