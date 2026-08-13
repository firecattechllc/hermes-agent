"""Real (non-fake) :class:`~runway.omniroute_port.ExecutionPort` adapters.

Deliberately named ``runway.providers`` — distinct from the top-level
``providers/`` package (Hermes's live, production ``ProviderProfile``
gateway). Nothing here imports from that package, or from
``hermes_cli.prime``: every adapter in this subpackage is self-contained and
talks to its upstream over plain HTTP, gated by
:class:`~runway.flags.RunwayFeatureFlags.external_execution_enabled`, which
cannot currently be set ``True`` (see ``runway/flags.py``). Constructing an
adapter here does not make it reachable from :class:`~runway.router.RunwayRouter`
— nothing wires these into the router's decision path.
"""

from __future__ import annotations
