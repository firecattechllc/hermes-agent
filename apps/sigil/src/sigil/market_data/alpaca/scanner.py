"""Deterministic bounded delayed-SIP historical scanner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from ..models import MarketDataKind, MarketDataObservation
from ..policy import MarketDataPolicy, MarketDataPolicyError


@dataclass(frozen=True, slots=True)
class ScanCheckpoint:
    universe_digest: str
    next_batch: int
    total_batches: int
    completed_batches: tuple[int, ...]
    incomplete_batches: tuple[int, ...] = ()


class DelayedSipScanner:
    def __init__(self, policy: MarketDataPolicy, fetch: Callable[..., object]) -> None:
        self.policy, self.fetch = policy, fetch

    def scan(
        self, instruments: Iterable[tuple[str, str]], *, now: datetime,
        checkpoint: ScanCheckpoint | None = None
    ) -> tuple[tuple[MarketDataObservation, ...], ScanCheckpoint, dict[str, int]]:
        ordered = tuple(sorted(set(instruments), key=lambda item: (item[1], item[0])))
        digest = hashlib.sha256(json.dumps(ordered, separators=(",", ":")).encode()).hexdigest()
        batches = tuple(
            ordered[index:index + self.policy.batch_size]
            for index in range(0, len(ordered), self.policy.batch_size)
        )
        start_batch = checkpoint.next_batch if checkpoint and checkpoint.universe_digest == digest else 0
        completed = list(checkpoint.completed_batches if checkpoint and checkpoint.universe_digest == digest else ())
        observations: list[MarketDataObservation] = []
        counts = {"successful": 0, "missing": 0, "rejected": 0, "stale": 0}
        cutoff = now.astimezone(timezone.utc) - timedelta(seconds=self.policy.delayed_sip_seconds)
        start = cutoff - timedelta(minutes=2)
        for batch_index in range(start_batch, len(batches)):
            batch = batches[batch_index]
            response = self.fetch(
                tuple(symbol for _, symbol in batch),
                start=start.isoformat().replace("+00:00", "Z"),
                end=cutoff.isoformat().replace("+00:00", "Z"),
            )
            bars = response.get("bars", {}) if isinstance(response, dict) else {}
            for instrument_id, symbol in batch:
                values = bars.get(symbol) if isinstance(bars, dict) else None
                raw = values[-1] if isinstance(values, list) and values else None
                if not isinstance(raw, dict):
                    counts["missing"] += 1
                    continue
                received = now.isoformat().replace("+00:00", "Z")
                timestamp = raw.get("t")
                try:
                    self.policy.validate_delayed_timestamp(timestamp, received)
                    close = self.policy.decimal(raw.get("c"), "price")
                    volume = self.policy.decimal(raw.get("v"), "size")
                except MarketDataPolicyError:
                    counts["rejected"] += 1
                    continue
                evidence = hashlib.sha256(
                    json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                observations.append(MarketDataObservation(
                    observation_id=f"alpaca-sip-{symbol}-{timestamp}",
                    instrument_id=instrument_id, kind=MarketDataKind.BAR,
                    field_name="close_price", value=str(close), unit="USD",
                    observed_at=timestamp, received_at=received, source_id="alpaca",
                    evidence_references=(f"sha256:{evidence}",),
                    symbol=symbol, provider="alpaca", feed="sip",
                    observation_type="historical_bar",
                    price_fields=(("close", str(close)),),
                    volume_fields=(("volume", str(volume)),), bar_timeframe="1Min",
                    classification="delayed", expected_delay_seconds=self.policy.delayed_sip_seconds,
                    evidence_digest=evidence, quality_flags=("delayed",),
                ))
                counts["successful"] += 1
            completed.append(batch_index)
        return tuple(observations), ScanCheckpoint(digest, len(batches), len(batches), tuple(completed)), counts
