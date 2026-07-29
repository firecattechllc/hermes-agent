"""Deterministic production scoring, shadow lifecycle, and validation."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sigil.asset_catalog import NormalizedAsset

from .models import (
    EvidenceStatus,
    MarketBar,
    MarketEvidence,
    ProductionStrategyPolicy,
    StrategyScore,
    canonical,
    decimal,
    parse_time,
)
from .store import ProductionResearchStore, now_text

MAX_RECORDS = 2_000
MAX_EVIDENCE_RECORDS = 200
PROMOTION_MINIMUM_OUTCOMES = 30
PROMOTION_MINIMUM_SYMBOLS = 10
PROMOTION_MINIMUM_DAYS = 14
PROMOTION_MINIMUM_COMPLETENESS = Decimal("0.90")
PROMOTION_MINIMUM_NET_RETURN = Decimal("-0.05")
PROMOTION_MAXIMUM_DRAWDOWN = Decimal("0.15")
MAXIMUM_PROVIDER_CLOCK_SKEW_SECONDS = 10
NEW_YORK = ZoneInfo("America/New_York")


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


def _return(current: Decimal, previous: Decimal) -> Decimal:
    return (current / previous) - Decimal(1)


def _daily_bar_is_stale(bar_timestamp: str, now: datetime) -> bool:
    """Treat the prior completed weekday bar as valid through the next session."""
    local_now = now.astimezone(NEW_YORK)
    expected = local_now.date()
    if local_now.time() < time(16, 15):
        expected -= timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    bar_time = parse_time(bar_timestamp, "latest bar")
    if bar_time > now.astimezone(UTC):
        return True
    return bar_time.astimezone(NEW_YORK).date() < expected


def _audit(state: dict[str, Any], event: str, evidence_id: str, details: dict[str, Any]) -> None:
    sequence = len(state["audit"]) + 1
    state["audit"].insert(
        0,
        {
            "audit_id": f"SIGIL-V21-AUD-{sequence:08d}",
            "evidence_id": evidence_id,
            "event": event,
            "timestamp": now_text(),
            "environment": "paper",
            "live_execution": False,
            "details": details,
        },
    )
    del state["audit"][MAX_RECORDS:]


class ProductionResearchService:
    def __init__(
        self,
        store: ProductionResearchStore,
        *,
        policy: ProductionStrategyPolicy | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or ProductionStrategyPolicy()

    def score(
        self,
        asset: NormalizedAsset | dict[str, Any],
        evidence: MarketEvidence,
        *,
        now: datetime,
        catalog_fresh: bool = True,
        portfolio_fresh: bool = True,
        market_state_known: bool = True,
        paused: bool = False,
        kill_switch: bool = False,
        audit_available: bool = True,
        reconciliation_complete: bool = True,
        duplicate_position: bool = False,
        duplicate_order: bool = False,
    ) -> StrategyScore:
        values = asset.to_dict() if hasattr(asset, "to_dict") else dict(asset)
        reasons = list(evidence.missing_classifications)
        if evidence.demonstration:
            reasons.append("demonstration_evidence_forbidden")
        if evidence.status is not EvidenceStatus.COMPLETE:
            reasons.append(f"evidence_{evidence.status.value}")
        if values.get("asset_class") != "us_equity":
            reasons.append("unsupported_asset")
        if values.get("status") != "active":
            reasons.append("inactive_asset")
        if values.get("tradable") is not True:
            reasons.append("not_tradable")
        if values.get("fractionable") is not True:
            reasons.append("not_fractionable")
        if str(values.get("exchange", "")).upper().startswith("OTC"):
            reasons.append("otc_forbidden")
        name = str(values.get("name", "")).upper()
        if any(term in name for term in ("2X", "3X", "ULTRA", "INVERSE", "BEAR")):
            reasons.append("leveraged_or_inverse_forbidden")
        if not catalog_fresh:
            reasons.append("stale_catalog")
        if not portfolio_fresh:
            reasons.append("stale_portfolio")
        if not market_state_known:
            reasons.append("unknown_market_state")
        if paused:
            reasons.append("execution_paused")
        if kill_switch:
            reasons.append("kill_switch_active")
        if not audit_available:
            reasons.append("audit_unavailable")
        if not reconciliation_complete:
            reasons.append("reconciliation_required")
        if duplicate_position:
            reasons.append("duplicate_position")
        if duplicate_order:
            reasons.append("duplicate_open_order")
        if evidence.bid is None or evidence.ask is None:
            reasons.append("missing_quote")
        bars = list(evidence.daily_bars)
        if len(bars) < self.policy.minimum_history_bars:
            reasons.append("insufficient_history")
        quote_age = (
            now.astimezone(UTC) - parse_time(evidence.observed_at, "quote")
        ).total_seconds()
        if (
            quote_age < -MAXIMUM_PROVIDER_CLOCK_SKEW_SECONDS
            or quote_age > self.policy.maximum_quote_age_seconds
        ):
            reasons.append("stale_quote")
        if bars and _daily_bar_is_stale(bars[-1].timestamp, now):
            reasons.append("stale_bars")
        if reasons or evidence.bid is None or evidence.ask is None or len(bars) < 2:
            return self._rejected(evidence, now, reasons)

        midpoint = (evidence.bid + evidence.ask) / Decimal(2)
        spread_bps = ((evidence.ask - evidence.bid) / midpoint) * Decimal(10000)
        closes = [bar.close for bar in bars]
        volumes = [bar.volume for bar in bars]
        short_average = _mean(closes[-self.policy.short_average_bars :])
        medium_average = _mean(closes[-self.policy.medium_average_bars :])
        momentum = _return(closes[-1], closes[-1 - self.policy.momentum_lookback_bars])
        reversal = _return(closes[-1], closes[-1 - self.policy.reversal_lookback_bars])
        returns = [
            _return(closes[index], closes[index - 1])
            for index in range(max(1, len(closes) - 20), len(closes))
        ]
        average_return = _mean(returns)
        variance = _mean([(value - average_return) ** 2 for value in returns])
        volatility = variance.sqrt() * Decimal(252).sqrt()
        average_dollar_volume = _mean(
            [
                bars[index].close * bars[index].volume
                for index in range(max(0, len(bars) - 20), len(bars))
            ]
        )
        comparison_volumes = volumes[-21:-1] or volumes[:-1]
        relative_volume = (
            volumes[-1] / _mean(comparison_volumes)
            if comparison_volumes and _mean(comparison_volumes) > 0
            else Decimal(0)
        )
        recent_high = max(bar.high for bar in bars[-20:])
        drawdown = _return(closes[-1], recent_high)
        gap = abs(_return(bars[-1].open, closes[-2]))
        stability = _mean([abs(value) for value in returns])

        hard = list(reasons)
        if closes[-1] < self.policy.minimum_price:
            hard.append("price_below_minimum")
        if spread_bps > self.policy.maximum_spread_bps:
            hard.append("excessive_spread")
        if average_dollar_volume < self.policy.minimum_average_dollar_volume:
            hard.append("insufficient_liquidity")
        if volatility > self.policy.maximum_annualized_volatility:
            hard.append("excessive_volatility")
        if gap > self.policy.maximum_gap:
            hard.append("extreme_gap")

        components = {
            "trend_alignment": (
                Decimal(20)
                if closes[-1] > medium_average and short_average > medium_average
                else Decimal(0)
            ),
            "momentum": (
                Decimal(20)
                if Decimal("0.01") <= momentum <= self.policy.maximum_twenty_day_momentum
                else Decimal(5)
                if momentum > 0
                else Decimal(0)
            ),
            "reversal_risk": (
                Decimal(10) if reversal <= self.policy.maximum_five_day_gain else Decimal(0)
            ),
            "volatility": (
                Decimal(10)
                if volatility <= Decimal("0.50")
                else Decimal(5)
                if volatility <= self.policy.maximum_annualized_volatility
                else Decimal(0)
            ),
            "liquidity": (
                Decimal(10)
                if average_dollar_volume >= self.policy.minimum_average_dollar_volume * Decimal(4)
                else Decimal(7)
            ),
            "spread": (
                Decimal(10)
                if spread_bps <= self.policy.maximum_spread_bps / Decimal(2)
                else Decimal(5)
            ),
            "relative_volume": (
                Decimal(10)
                if relative_volume >= Decimal(1)
                else Decimal(6)
                if relative_volume >= self.policy.minimum_relative_volume
                else Decimal(0)
            ),
            "drawdown": Decimal(5) if drawdown >= Decimal("-0.10") else Decimal(0),
            "price_stability": (Decimal(5) if stability <= Decimal("0.03") else Decimal(0)),
        }
        soft: list[str] = []
        if momentum > self.policy.maximum_twenty_day_momentum:
            soft.append("momentum_extended")
        if relative_volume < self.policy.minimum_relative_volume:
            soft.append("relative_volume_below_preference")
        if drawdown < Decimal("-0.10"):
            soft.append("recent_drawdown")
        if reversal > self.policy.maximum_five_day_gain:
            soft.append("short_term_reversal_risk")

        total = sum(components.values(), Decimal(0))
        normalized = total / Decimal(100)
        confidence = min(
            Decimal(1),
            Decimal(len(bars)) / Decimal(60),
        )
        if normalized < self.policy.minimum_normalized_score:
            hard.append("score_below_threshold")
        if confidence < self.policy.minimum_confidence:
            hard.append("confidence_below_threshold")
        eligible = not hard
        component_evidence = {
            "last_price": str(closes[-1]),
            "short_moving_average": str(_quantize(short_average)),
            "medium_moving_average": str(_quantize(medium_average)),
            "twenty_day_return": str(_quantize(momentum)),
            "five_day_return": str(_quantize(reversal)),
            "annualized_volatility": str(_quantize(volatility)),
            "average_dollar_volume": str(_quantize(average_dollar_volume)),
            "relative_volume": str(_quantize(relative_volume)),
            "spread_bps": str(_quantize(spread_bps)),
            "drawdown_from_recent_high": str(_quantize(drawdown)),
            "opening_gap": str(_quantize(gap)),
            "average_absolute_return": str(_quantize(stability)),
        }
        return StrategyScore(
            strategy_id=self.policy.strategy_id,
            strategy_version=self.policy.strategy_version,
            symbol=evidence.symbol,
            timestamp=now.isoformat().replace("+00:00", "Z"),
            total_score=total,
            normalized_score=_quantize(normalized),
            confidence=_quantize(confidence),
            component_scores=tuple(sorted(components.items())),
            component_evidence=tuple(sorted(component_evidence.items())),
            hard_rejection_reasons=tuple(sorted(set(hard))),
            soft_penalties=tuple(sorted(soft)),
            eligible=eligible,
            proposal_recommendation="buy" if eligible else "none",
            evidence_checksum=evidence.evidence_checksum,
            average_dollar_volume=_quantize(average_dollar_volume),
            spread_bps=_quantize(spread_bps),
        )

    def _rejected(
        self, evidence: MarketEvidence, now: datetime, reasons: list[str]
    ) -> StrategyScore:
        return StrategyScore(
            strategy_id=self.policy.strategy_id,
            strategy_version=self.policy.strategy_version,
            symbol=evidence.symbol,
            timestamp=now.isoformat().replace("+00:00", "Z"),
            total_score=Decimal(0),
            normalized_score=Decimal(0),
            confidence=Decimal(0),
            component_scores=(),
            component_evidence=(
                ("evidence_status", evidence.status.value),
                ("missing", ",".join(evidence.missing_classifications)),
            ),
            hard_rejection_reasons=tuple(sorted(set(reasons))),
            soft_penalties=(),
            eligible=False,
            proposal_recommendation="none",
            evidence_checksum=evidence.evidence_checksum,
            average_dollar_volume=Decimal(0),
            spread_bps=Decimal(0),
        )

    def process_batch(
        self,
        assets: list[NormalizedAsset | dict[str, Any]],
        evidence: tuple[MarketEvidence, ...],
        *,
        cursor: int,
        batch_number: int,
        total_eligible: int,
        now: datetime,
        next_cycle_at: str | None,
        catalog_fresh: bool,
        portfolio_fresh: bool,
        market_state_known: bool,
        paused: bool,
        kill_switch: bool,
        audit_available: bool,
        reconciliation_complete: bool,
        provider_status: str = "available",
        market_data_freshness: str = "fresh",
    ) -> dict[str, Any]:
        if len(assets) > 25 or len(evidence) > 25:
            raise ValueError("production research batches are bounded to 25 symbols")
        by_symbol = {item.symbol: item for item in evidence}
        scores = [
            self.score(
                asset,
                by_symbol.get(
                    str(asset.symbol if hasattr(asset, "symbol") else asset.get("symbol"))
                )
                or self.unavailable_evidence(
                    str(asset.symbol if hasattr(asset, "symbol") else asset.get("symbol")),
                    now,
                ),
                now=now,
                catalog_fresh=catalog_fresh,
                portfolio_fresh=portfolio_fresh,
                market_state_known=market_state_known,
                paused=paused,
                kill_switch=kill_switch,
                audit_available=audit_available,
                reconciliation_complete=reconciliation_complete,
            )
            for asset in assets
        ]
        ranked = sorted(
            (score for score in scores if score.eligible), key=lambda x: x.ranking_key()
        )
        failures = Counter(reason for score in scores for reason in score.hard_rejection_reasons)
        with self.store.locked() as state:
            state["strategy"] = self.policy.to_dict()
            state["progress"].update(
                {
                    "state": ("proposal_generated" if ranked else "no_eligible_candidate"),
                    "current_batch": batch_number,
                    "current_cursor": cursor,
                    "symbols_in_batch": [
                        str(
                            asset.symbol
                            if hasattr(asset, "symbol")
                            else asset.get("symbol")
                        )
                        for asset in assets
                    ],
                    "total_eligible": total_eligible,
                    "symbols_researched": len(scores),
                    "research_successes": sum(score.total_score > 0 for score in scores),
                    "research_failures": sum(
                        bool(score.hard_rejection_reasons) for score in scores
                    ),
                    "scored_count": sum(score.total_score > 0 for score in scores),
                    "hard_rejected_count": sum(
                        bool(score.hard_rejection_reasons) for score in scores
                    ),
                    "evidence_complete_count": sum(
                        item.status is EvidenceStatus.COMPLETE for item in evidence
                    ),
                    "evidence_incomplete_count": sum(
                        item.status is not EvidenceStatus.COMPLETE for item in evidence
                    ),
                    "evidence_completeness": str(
                        _quantize(
                            Decimal(
                                sum(item.status is EvidenceStatus.COMPLETE for item in evidence)
                            )
                            / Decimal(max(1, len(assets)))
                        )
                    ),
                    "candidates_produced": len(ranked),
                    "proposals_generated": 1 if ranked else 0,
                    "leading_rejection_reasons": dict(failures.most_common(8)),
                    "last_completed_research": now.isoformat().replace("+00:00", "Z"),
                    "provider_status": provider_status,
                    "market_data_freshness": market_data_freshness,
                    "next_cycle_at": next_cycle_at,
                }
            )
            records = [
                {
                    **score.to_dict(),
                    "market_evidence": by_symbol[score.symbol].to_dict(),
                }
                for score in scores
            ]
            state["research_results"] = records + state["research_results"]
            state["candidates"] = [score.to_dict() for score in ranked] + state["candidates"]
            proposal = None
            if ranked:
                proposal = self._proposal(ranked[0], by_symbol[ranked[0].symbol], now)
                state["proposals"].insert(0, proposal)
                if state["shadow_mode"]:
                    self._admit_shadow(state, proposal)
            del state["research_results"][MAX_EVIDENCE_RECORDS:]
            for key in ("candidates", "proposals"):
                del state[key][MAX_RECORDS:]
            evidence_id = f"SIGIL-V21-BATCH-{batch_number}-{cursor}"
            _audit(
                state,
                "production_research_batch_completed",
                evidence_id,
                {
                    "symbols": [score.symbol for score in scores],
                    "eligible": len(ranked),
                    "proposal_id": proposal["proposal_id"] if proposal else None,
                    "shadow_mode": state["shadow_mode"],
                    "broker_submission_attempted": False,
                },
            )
            self.store.save(state)
            projection = self._projection(state)
            projection["last_proposal"] = dict(proposal) if proposal else None
            return projection

    @staticmethod
    def unavailable_evidence(
        symbol: str, now: datetime, reason: str = "provider_data_unavailable"
    ) -> MarketEvidence:
        value = now.isoformat().replace("+00:00", "Z")
        status = {
            "credentials_unavailable": EvidenceStatus.UNAVAILABLE,
            "authentication_failed": EvidenceStatus.PROVIDER_ERROR,
            "rate_limited": EvidenceStatus.RATE_LIMITED,
            "malformed": EvidenceStatus.MALFORMED,
            "provider_request_rejected": EvidenceStatus.UNSUPPORTED,
        }.get(reason, EvidenceStatus.PROVIDER_ERROR)
        return MarketEvidence(
            symbol=symbol,
            observed_at=value,
            received_at=value,
            source="alpaca_market_data",
            feed="iex",
            adjustment="all",
            status=status,
            bid=None,
            ask=None,
            bid_size=None,
            ask_size=None,
            last_trade=None,
            last_trade_at=None,
            daily_bars=(),
            missing_classifications=(reason,),
        )

    def _proposal(
        self, score: StrategyScore, evidence: MarketEvidence, now: datetime
    ) -> dict[str, Any]:
        assert evidence.ask is not None
        material = {
            "strategy": self.policy.strategy_id,
            "version": self.policy.strategy_version,
            "symbol": score.symbol,
            "evidence_checksum": score.evidence_checksum,
        }
        proposal_id = f"SIGIL-V21-PRP-{hashlib.sha256(canonical(material)).hexdigest()[:24]}"
        expiration = now + timedelta(seconds=self.policy.proposal_ttl_seconds)
        return {
            "proposal_id": proposal_id,
            "strategy_id": score.strategy_id,
            "strategy_version": score.strategy_version,
            "symbol": score.symbol,
            "side": "buy",
            "proposed_notional": "25.00",
            "reference_price": str(evidence.ask),
            "entry_rationale": "Validated liquid-trend evidence passed all hard gates.",
            "score": score.to_dict(),
            "confidence": str(score.confidence),
            "risks": list(score.soft_penalties),
            "invalidation_conditions": [
                "strategy_score_below_threshold",
                "stale_quote",
                "spread_above_limit",
                "trend_alignment_lost",
            ],
            "exit_plan": {
                "stop_loss_percent": str(self.policy.stop_loss_percent),
                "take_profit_percent": str(self.policy.take_profit_percent),
                "maximum_holding_days": self.policy.maximum_holding_days,
            },
            "evidence_identity": score.evidence_checksum,
            "market_data_timestamps": {
                "quote": evidence.observed_at,
                "latest_bar": evidence.daily_bars[-1].timestamp,
            },
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": expiration.isoformat().replace("+00:00", "Z"),
            "status": "generated",
            "audit_identity": f"SIGIL-V21-PROPOSAL-{proposal_id}",
        }

    def _admit_shadow(self, state: dict[str, Any], proposal: dict[str, Any]) -> None:
        ask = decimal(proposal["reference_price"], "reference price")
        slippage = self.policy.shadow_slippage_bps / Decimal(10000)
        fill = _quantize(ask * (Decimal(1) + slippage))
        proposal["status"] = "admitted_in_shadow"
        state["shadow_positions"].insert(
            0,
            {
                "shadow_id": f"shadow-{proposal['proposal_id']}",
                "proposal_id": proposal["proposal_id"],
                "symbol": proposal["symbol"],
                "strategy_version": proposal["strategy_version"],
                "status": "monitoring",
                "entry_reference": proposal["reference_price"],
                "hypothetical_fill": str(fill),
                "entry_at": proposal["created_at"],
                "maximum_favorable_excursion": "0",
                "maximum_adverse_excursion": "0",
                "estimated_slippage": str(_quantize(fill - ask)),
                "last_monitor_at": proposal["created_at"],
                "evidence_identity": proposal["evidence_identity"],
            },
        )
        del state["shadow_positions"][MAX_RECORDS:]

    def monitor_shadow(
        self, evidence: tuple[MarketEvidence, ...], *, now: datetime
    ) -> dict[str, Any]:
        by_symbol = {item.symbol: item for item in evidence}
        with self.store.locked() as state:
            remaining = []
            for position in state["shadow_positions"]:
                item = by_symbol.get(position["symbol"])
                if item is None or item.status is not EvidenceStatus.COMPLETE or item.bid is None:
                    position["status"] = "insufficient_followup_data"
                    position["last_monitor_at"] = now.isoformat().replace("+00:00", "Z")
                    remaining.append(position)
                    continue
                entry = decimal(position["hypothetical_fill"], "shadow entry")
                exit_reference = item.bid
                gross_return = _return(exit_reference, entry)
                position["maximum_favorable_excursion"] = str(
                    max(
                        decimal(position["maximum_favorable_excursion"], "mfe"),
                        gross_return,
                    )
                )
                position["maximum_adverse_excursion"] = str(
                    min(
                        decimal(position["maximum_adverse_excursion"], "mae"),
                        gross_return,
                    )
                )
                entered = parse_time(position["entry_at"], "shadow entry")
                holding = now.astimezone(UTC) - entered
                trigger = None
                if gross_return <= -self.policy.stop_loss_percent:
                    trigger = "protective_stop"
                elif gross_return >= self.policy.take_profit_percent:
                    trigger = "profit_taking"
                elif holding >= timedelta(days=self.policy.maximum_holding_days):
                    trigger = "maximum_holding_period"
                if trigger is None:
                    position["status"] = "monitoring"
                    position["last_monitor_at"] = now.isoformat().replace("+00:00", "Z")
                    remaining.append(position)
                    continue
                exit_slippage = self.policy.shadow_slippage_bps / Decimal(10000)
                hypothetical_exit = _quantize(exit_reference * (Decimal(1) - exit_slippage))
                net_return = _quantize(_return(hypothetical_exit, entry))
                state["shadow_outcomes"].insert(
                    0,
                    {
                        **position,
                        "status": "closed",
                        "exit_trigger": trigger,
                        "exit_reference": str(exit_reference),
                        "hypothetical_exit": str(hypothetical_exit),
                        "holding_seconds": int(holding.total_seconds()),
                        "gross_return": str(_quantize(gross_return)),
                        "net_simulated_return": str(net_return),
                        "outcome_classification": (
                            "positive"
                            if net_return > 0
                            else "negative"
                            if net_return < 0
                            else "flat"
                        ),
                        "closed_at": now.isoformat().replace("+00:00", "Z"),
                    },
                )
            state["shadow_positions"] = remaining
            del state["shadow_outcomes"][MAX_RECORDS:]
            _audit(
                state,
                "shadow_positions_monitored",
                f"SIGIL-V21-SHADOW-{state['revision']}",
                {
                    "active": len(remaining),
                    "completed": len(state["shadow_outcomes"]),
                    "broker_submission_attempted": False,
                },
            )
            self.store.save(state)
            return self._projection(state)

    def promotion_readiness(self) -> dict[str, Any]:
        state = self.store.load()
        summary = self._promotion_summary(state)
        return {
            **self._identity(state),
            **summary,
            "informative_only": True,
            "profit_guarantee": False,
        }

    def request_promotion(self) -> dict[str, Any]:
        readiness = self.promotion_readiness()
        if not readiness["ready"]:
            raise ValueError(
                "paper promotion is not ready: " + ",".join(readiness["failed_conditions"])
            )
        with self.store.locked() as state:
            state["paper_promotion_approved"] = True
            _audit(
                state,
                "paper_promotion_approved",
                f"SIGIL-V21-PROMOTION-{state['revision']}",
                {"readiness": readiness},
            )
            self.store.save(state)
            return self._projection(state)

    def set_shadow_mode(self, enabled: bool) -> dict[str, Any]:
        if not enabled and not self.promotion_readiness()["ready"]:
            raise ValueError("shadow mode cannot be disabled before promotion readiness")
        with self.store.locked() as state:
            state["shadow_mode"] = bool(enabled)
            _audit(
                state,
                "shadow_mode_enabled" if enabled else "shadow_mode_disabled",
                f"SIGIL-V21-SHADOW-MODE-{state['revision']}",
                {},
            )
            self.store.save(state)
            return self._projection(state)

    def validation_report(
        self,
        bars_by_symbol: dict[str, tuple[MarketBar, ...]],
        *,
        dataset_identity: str,
        slippage_bps: Decimal | None = None,
    ) -> dict[str, Any]:
        """Chronological walk-forward approximation; decisions use prior bars only."""
        slippage = slippage_bps or self.policy.shadow_slippage_bps
        observations = 0
        signals: list[dict[str, Any]] = []
        for symbol in sorted(bars_by_symbol):
            bars = bars_by_symbol[symbol]
            for index in range(self.policy.minimum_history_bars, len(bars) - 1):
                history = bars[:index]
                observations += 1
                short = _mean([item.close for item in history[-self.policy.short_average_bars :]])
                medium = _mean([item.close for item in history[-self.policy.medium_average_bars :]])
                if history[-1].close <= medium or short <= medium:
                    continue
                entry = history[-1].close * (Decimal(1) + slippage / Decimal(10000))
                exit_price = bars[index].close * (Decimal(1) - slippage / Decimal(10000))
                signals.append(
                    {
                        "symbol": symbol,
                        "decision_index": index - 1,
                        "exit_index": index,
                        "return": str(_quantize(_return(exit_price, entry))),
                    }
                )
        returns = [decimal(item["return"], "validation return") for item in signals]
        gross = sum(returns, Decimal(0))
        wins = sum(value > 0 for value in returns)
        losses = [-value for value in returns if value < 0]
        gains = [value for value in returns if value > 0]
        report_core = {
            "strategy_id": self.policy.strategy_id,
            "strategy_version": self.policy.strategy_version,
            "parameter_version": self.policy.strategy_version,
            "dataset_identity": dataset_identity,
            "dataset_checksum": hashlib.sha256(
                canonical(
                    {
                        symbol: [bar.to_dict() for bar in bars_by_symbol[symbol]]
                        for symbol in sorted(bars_by_symbol)
                    }
                )
            ).hexdigest(),
            "observations": observations,
            "candidate_signals": len(signals),
            "admitted_shadow_trades": len(signals),
            "win_rate": str(
                _quantize(Decimal(wins) / Decimal(len(signals))) if signals else Decimal(0)
            ),
            "average_return": str(_quantize(_mean(returns)) if returns else Decimal(0)),
            "median_return": str(sorted(returns)[len(returns) // 2] if returns else Decimal(0)),
            "gross_return": str(_quantize(gross)),
            "estimated_net_return": str(_quantize(gross)),
            "maximum_drawdown": str(abs(min(returns, default=Decimal(0)))),
            "maximum_adverse_excursion": str(min(returns, default=Decimal(0))),
            "maximum_favorable_excursion": str(max(returns, default=Decimal(0))),
            "average_holding_period_bars": "1",
            "profit_factor": str(
                _quantize(sum(gains, Decimal(0)) / sum(losses, Decimal(0)))
                if losses
                else "unavailable"
            ),
            "exposure": str(_quantize(Decimal(len(signals)) / Decimal(max(1, observations)))),
            "turnover": len(signals),
            "slippage_bps": str(slippage),
            "walk_forward": True,
            "chronological": True,
            "future_data_leakage": False,
            "survivorship_bias_caveat": True,
            "delisted_symbol_caveat": True,
            "signals": signals,
        }
        report = {
            **report_core,
            "report_checksum": hashlib.sha256(canonical(report_core)).hexdigest(),
        }
        with self.store.locked() as state:
            state["validation_reports"].insert(0, report)
            del state["validation_reports"][100:]
            self.store.save(state)
        return report

    def status(self) -> dict[str, Any]:
        return self._projection(self.store.load())

    def recent(self, kind: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        mapping = {
            "research": "research_results",
            "candidates": "candidates",
            "proposals": "proposals",
            "shadow_positions": "shadow_positions",
            "shadow_outcomes": "shadow_outcomes",
            "validation": "validation_reports",
            "audit": "audit",
        }
        if kind not in mapping:
            raise ValueError("unsupported production research collection")
        state = self.store.load()
        start = max(0, int(offset))
        size = min(100, max(1, int(limit)))
        values = state[mapping[kind]]
        return {
            **self._identity(state),
            "offset": start,
            "limit": size,
            "total": len(values),
            "has_more": start + size < len(values),
            "items": values[start : start + size],
        }

    def detail(self, kind: str, identity: str) -> dict[str, Any] | None:
        values = self.recent(kind, limit=100)["items"]
        keys = {
            "research": ("symbol", "evidence_checksum"),
            "candidates": ("symbol", "evidence_checksum"),
            "proposals": ("proposal_id",),
            "shadow_positions": ("shadow_id", "proposal_id"),
            "shadow_outcomes": ("shadow_id", "proposal_id"),
        }
        for item in values:
            if any(item.get(key) == identity for key in keys.get(kind, ())):
                return {**self._identity(self.store.load()), "item": item}
        return None

    def _identity(self, state: dict[str, Any]) -> dict[str, Any]:
        audit = state["audit"][0] if state["audit"] else {}
        return {
            "environment": "paper",
            "live_execution": False,
            "broker_submission": False,
            "shadow_mode": bool(state["shadow_mode"]),
            "strategy_id": self.policy.strategy_id,
            "strategy_version": self.policy.strategy_version,
            "revision": state["revision"],
            "evidence_identity": audit.get("evidence_id"),
            "audit_identity": audit.get("audit_id"),
            "degraded_conditions": list(state["safety_defects"]),
        }

    def _projection(self, state: dict[str, Any]) -> dict[str, Any]:
        outcomes = state["shadow_outcomes"]
        returns = [decimal(item["net_simulated_return"], "shadow return") for item in outcomes]
        return {
            **self._identity(state),
            "paper_promotion_approved": bool(state["paper_promotion_approved"]),
            "strategy": state["strategy"] or self.policy.to_dict(),
            "progress": state["progress"],
            "research_result_count": len(state["research_results"]),
            "candidate_count": len(state["candidates"]),
            "proposal_count": len(state["proposals"]),
            "active_shadow_positions": len(state["shadow_positions"]),
            "completed_shadow_outcomes": len(outcomes),
            "shadow_simulated_return": str(_quantize(sum(returns, Decimal(0)))),
            "shadow_win_rate": str(
                _quantize(Decimal(sum(value > 0 for value in returns)) / Decimal(len(returns)))
                if returns
                else Decimal(0)
            ),
            "promotion": self._promotion_summary(state),
        }

    def _promotion_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        outcomes = state["shadow_outcomes"]
        symbols = {item["symbol"] for item in outcomes}
        timestamps = sorted(parse_time(item["entry_at"], "shadow entry") for item in outcomes)
        duration = (timestamps[-1] - timestamps[0]).days if len(timestamps) > 1 else 0
        net_returns = [decimal(item["net_simulated_return"], "net return") for item in outcomes]
        total_net = sum(net_returns, Decimal(0))
        running = Decimal(0)
        peak = Decimal(0)
        maximum_drawdown = Decimal(0)
        for value in reversed(net_returns):
            running += value
            peak = max(peak, running)
            maximum_drawdown = max(maximum_drawdown, peak - running)
        researched = len(state["research_results"])
        complete = sum(
            item.get("market_evidence", {}).get("status") == "complete"
            for item in state["research_results"]
        )
        completeness = Decimal(complete) / Decimal(researched) if researched else Decimal(0)
        checks = {
            "minimum_completed_shadow_proposals": len(outcomes) >= PROMOTION_MINIMUM_OUTCOMES,
            "minimum_distinct_symbols": len(symbols) >= PROMOTION_MINIMUM_SYMBOLS,
            "minimum_observation_days": duration >= PROMOTION_MINIMUM_DAYS,
            "no_unresolved_safety_defects": not state["safety_defects"],
            "data_completeness": completeness >= PROMOTION_MINIMUM_COMPLETENESS,
            "net_performance_tolerance": total_net >= PROMOTION_MINIMUM_NET_RETURN,
            "maximum_drawdown_tolerance": maximum_drawdown <= PROMOTION_MAXIMUM_DRAWDOWN,
        }
        failed = [key for key, passed in checks.items() if not passed]
        return {
            "status": "ready" if not failed else "promotion_not_ready",
            "ready": not failed,
            "failed_conditions": failed,
            "checks": checks,
            "completed_shadow_proposals": len(outcomes),
            "distinct_symbols": len(symbols),
            "observation_days": duration,
            "data_completeness_rate": str(_quantize(completeness)),
            "net_simulated_return": str(_quantize(total_net)),
            "maximum_drawdown": str(_quantize(maximum_drawdown)),
        }
