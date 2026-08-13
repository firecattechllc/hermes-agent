"""Feature flags (spec section 21).

Follows the repo's existing convention (a plain ``enabled: bool`` field on a
frozen config object — see ``gateway/config.py:PlatformConfig.enabled``)
rather than inventing a separate flags registry.

Project Runway is disabled by default end-to-end. ``external_execution_enabled``
is the hard safety line: it is pinned to ``False`` by :meth:`RunwayFeatureFlags.from_env`
regardless of environment input, because in this build there is no real
execution adapter for it to enable anyway (see :mod:`runway.omniroute_port`).
Flipping it live is a separate, future certification step, not a config edit.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, model_validator


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class RunwayFeatureFlags(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runway_enabled: bool = False
    discovery_enabled: bool = False
    synthetic_qualification_enabled: bool = False
    external_execution_enabled: bool = False

    @model_validator(mode="after")
    def _external_execution_hard_locked(self) -> "RunwayFeatureFlags":
        if self.external_execution_enabled:
            raise ValueError(
                "external_execution_enabled cannot be set True in this build — "
                "there is no real execution adapter; live provider activation is a "
                "separate, future certification step (see docs/architecture/PROJECT_RUNWAY_FOUNDATION.md)"
            )
        return self

    @classmethod
    def from_env(cls) -> "RunwayFeatureFlags":
        return cls(
            runway_enabled=_env_bool("RUNWAY_ENABLED"),
            discovery_enabled=_env_bool("RUNWAY_DISCOVERY_ENABLED"),
            synthetic_qualification_enabled=_env_bool("RUNWAY_SYNTHETIC_QUALIFICATION_ENABLED"),
            external_execution_enabled=False,  # never read from the environment
        )

    @classmethod
    def all_disabled(cls) -> "RunwayFeatureFlags":
        return cls()
