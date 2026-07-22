"""Deterministic, non-executing scheduling recommendations."""

from __future__ import annotations

from typing import Iterable, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchedulingUnit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    unit_id: str
    dependencies: Tuple[str, ...] = ()
    conflict_domains: Tuple[str, ...] = ()
    exclusive_resources: Tuple[str, ...] = ()
    required_trust_tier: int = Field(default=0, ge=0, le=3)
    available_trust_tier: int = Field(default=3, ge=0, le=3)
    estimated_cost_micros: int = Field(default=0, ge=0)
    high_risk: bool = False
    approval_gated: bool = False
    cancellation_boundary: bool = True

    @model_validator(mode="after")
    def _not_self_dependent(self) -> "SchedulingUnit":
        if self.unit_id in self.dependencies:
            raise ValueError("scheduling unit cannot depend on itself")
        return self


class SchedulingWave(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sequence: int = Field(..., ge=1)
    unit_ids: Tuple[str, ...]


class SchedulingPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    waves: Tuple[SchedulingWave, ...]
    blocked_unit_ids: Tuple[str, ...]
    maximum_parallel_width: int = Field(..., ge=0)
    estimated_cost_micros: int = Field(..., ge=0)
    reason_codes: Tuple[str, ...]


def schedule_units(units: Iterable[SchedulingUnit], *, concurrency_limit: int, budget_limit_micros: int) -> SchedulingPlan:
    if concurrency_limit < 1 or budget_limit_micros < 0:
        raise ValueError("invalid scheduling limits")
    items = tuple(units)
    mapping = {item.unit_id: item for item in items}
    if len(mapping) != len(items) or any(dep not in mapping for item in items for dep in item.dependencies):
        raise ValueError("duplicate unit or unknown dependency")
    blocked = {item.unit_id for item in items if item.approval_gated or item.available_trust_tier < item.required_trust_tier}
    pending = set(mapping) - blocked
    complete: set[str] = set()
    waves = []
    cost = 0
    while pending:
        ready = [mapping[key] for key in sorted(pending) if set(mapping[key].dependencies) <= complete]
        if not ready:
            blocked.update(pending)
            break
        wave = []
        domains: set[str] = set()
        resources: set[str] = set()
        for item in ready:
            conflict = domains.intersection(item.conflict_domains) or resources.intersection(item.exclusive_resources)
            serial = item.high_risk or any(mapping[key].high_risk for key in wave)
            if wave and (conflict or serial):
                continue
            if len(wave) >= concurrency_limit or cost + item.estimated_cost_micros > budget_limit_micros:
                continue
            wave.append(item.unit_id)
            domains.update(item.conflict_domains)
            resources.update(item.exclusive_resources)
            cost += item.estimated_cost_micros
        if not wave:
            blocked.update(pending)
            break
        waves.append(SchedulingWave(sequence=len(waves) + 1, unit_ids=tuple(wave)))
        complete.update(wave)
        pending.difference_update(wave)
    width = max((len(w.unit_ids) for w in waves), default=0)
    reasons = ["deterministic_dependency_order", "high_risk_serialized"]
    if blocked:
        reasons.append("units_blocked_by_governance_or_resources")
    return SchedulingPlan(waves=tuple(waves), blocked_unit_ids=tuple(sorted(blocked)), maximum_parallel_width=width, estimated_cost_micros=cost, reason_codes=tuple(reasons))
