"""Governed deterministic IEX candidate rotation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable

from ..policy import MarketDataPolicy, MarketDataPolicyError


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    instrument_id: str
    symbol: str
    rank: int
    rank_reason: str


class IexStreamManager:
    def __init__(
        self, policy: MarketDataPolicy, *, subscribe: Callable[[tuple[str, ...]], None],
        unsubscribe: Callable[[tuple[str, ...]], None], minimum_dwell_seconds: int = 60,
        cooldown_seconds: int = 60,
    ) -> None:
        self.policy, self.subscribe, self.unsubscribe = policy, subscribe, unsubscribe
        self.minimum_dwell = timedelta(seconds=minimum_dwell_seconds)
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.active: tuple[str, ...] = ()
        self._added: dict[str, datetime] = {}
        self._removed: dict[str, datetime] = {}

    def rotate(self, candidates: Iterable[RankedCandidate], *, now: datetime) -> tuple[str, ...]:
        deduplicated: dict[str, RankedCandidate] = {}
        for candidate in candidates:
            symbol = candidate.symbol.strip().upper()
            prior = deduplicated.get(symbol)
            if prior is None or (candidate.rank, candidate.instrument_id) < (prior.rank, prior.instrument_id):
                deduplicated[symbol] = RankedCandidate(candidate.instrument_id, symbol, candidate.rank, candidate.rank_reason)
        ordered = tuple(sorted(deduplicated.values(), key=lambda item: (item.rank, item.symbol, item.instrument_id)))
        if len(ordered) > self.policy.iex_symbol_limit:
            raise MarketDataPolicyError("iex_capacity_rejected")
        target = tuple(item.symbol for item in ordered)
        retained = tuple(symbol for symbol in self.active if symbol in target or now - self._added[symbol] < self.minimum_dwell)
        available = self.policy.iex_symbol_limit - len(retained)
        additions = tuple(
            symbol for symbol in target if symbol not in retained
            and (symbol not in self._removed or now - self._removed[symbol] >= self.cooldown)
        )[:available]
        final = tuple(dict.fromkeys((*retained, *additions)))
        removals = tuple(symbol for symbol in self.active if symbol not in final)
        if removals:
            self.unsubscribe(removals)
            for symbol in removals:
                self._removed[symbol] = now
                self._added.pop(symbol, None)
        if additions:
            self.subscribe(additions)
            for symbol in additions:
                self._added[symbol] = now
        self.active = final
        return final

    def disconnect(self) -> None:
        if self.active:
            self.unsubscribe(self.active)
        self.active = ()
