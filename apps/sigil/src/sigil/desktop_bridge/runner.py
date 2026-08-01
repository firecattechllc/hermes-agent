"""Governed JSON command runner for Sigil Desktop.

The bridge exposes allow-listed local paper simulation controls and read-only
provider access. It has no broker execution, provider mutation, transfer,
wallet, shell, credential-return, or arbitrary command capability.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from typing import Any, Final

from sigil.market_universe import UniverseValidationError

from .alpaca_market_data import alpaca_market_data_status, control_alpaca_market_data
from .asset_catalog import (
    asset_catalog_exclusions,
    asset_catalog_refresh,
    asset_catalog_sample,
    asset_catalog_snapshot,
    asset_catalog_statistics,
    asset_catalog_status,
    research_universe_status,
)
from .autonomous_paper import (
    paper_execution_activate,
    paper_execution_collection,
    paper_execution_deactivate,
    paper_execution_emergency_stop,
    paper_execution_pause,
    paper_execution_resume,
    paper_execution_status,
    reconcile_paper_orders,
)
from .market_universe import market_universe_search, market_universe_status
from .market_quotes import market_universe_quotes
from .production_research import (
    emergency_paper_liquidation,
    production_research_collection,
    production_research_detail,
    production_research_status,
    promotion_readiness,
    reconcile_positions,
    request_paper_promotion,
    shadow_mode_disable,
    shadow_mode_enable,
)
from .governed_news_bridge import (
    governed_alpaca_news_collect,
    governed_news_advisory_summary,
    governed_news_status,
    governed_news_timeline,
)
from .providers import provider_snapshot
from .runtime import (
    control_paper_authorization,
    control_paper_cycle,
    reset_paper_runtime,
    runtime_snapshot,
)

BRIDGE_VERSION: Final[str] = "2.1"
SUPPORTED_COMMANDS: Final[tuple[str, ...]] = (
    "health",
    "explain_proposal",
    "runtime_snapshot",
    "control_paper_cycle",
    "control_paper_authorization",
    "reset_paper_runtime",
    "provider_snapshot",
    "governed_news_status",
    "governed_news_timeline",
    "governed_news_advisory_summary",
    "governed_alpaca_news_collect",
    "market_universe_status",
    "market_universe_search",
    "market_universe_quotes",
    "alpaca_market_data_status",
    "control_alpaca_market_data",
    "asset_catalog_status",
    "asset_catalog_refresh",
    "asset_catalog_snapshot",
    "asset_catalog_statistics",
    "asset_catalog_sample",
    "asset_catalog_exclusions",
    "research_universe_status",
    "research_universe_advance",
    "paper_execution_status",
    "paper_execution_activate",
    "paper_execution_deactivate",
    "paper_execution_pause",
    "paper_execution_resume",
    "paper_policy_status",
    "recent_candidates",
    "recent_proposals",
    "recent_rejections",
    "paper_order_intents",
    "paper_orders",
    "paper_positions",
    "paper_fills",
    "reconcile_paper_orders",
    "emergency_paper_stop",
    "production_research_status",
    "strategy_status",
    "current_batch_research",
    "recent_research_results",
    "candidate_detail",
    "proposal_detail",
    "shadow_mode_status",
    "shadow_mode_enable",
    "shadow_mode_disable",
    "shadow_positions",
    "shadow_outcomes",
    "shadow_performance",
    "promotion_readiness",
    "request_paper_promotion",
    "position_detail",
    "paper_exit_status",
    "reconcile_positions",
    "emergency_paper_liquidation",
)


def generated_at() -> str:
    """Return a UTC ISO-8601 timestamp."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def backend_status() -> dict[str, Any]:
    """Return deterministic, local-only Sigil backend status."""

    return {
        "bridge_version": BRIDGE_VERSION,
        "status": "ok",
        "mode": "local-read-only",
        "environment": "paper",
        "simulation": True,
        "execution_authorized": False,
        "broker_submission_available": False,
        "supported_commands": list(SUPPORTED_COMMANDS),
    }


def error_response(error: str, message: str) -> dict[str, Any]:
    """Return a consistent fail-closed response."""

    return {
        "ok": False,
        "error": error,
        "message": message,
    }


def valid_non_empty_string(value: object) -> bool:
    """Return whether a value is a non-empty string."""

    return isinstance(value, str) and bool(value.strip())


def validate_evidence_references(value: object) -> list[dict[str, str]] | None:
    """Validate and sanitize evidence references."""

    if not isinstance(value, list):
        return None

    references: list[dict[str, str]] = []

    for item in value:
        if not isinstance(item, dict):
            return None

        reference_id = item.get("id")
        if not valid_non_empty_string(reference_id):
            return None

        reference: dict[str, str] = {"id": reference_id.strip()}

        label = item.get("label")
        source = item.get("source")

        if label is not None:
            if not valid_non_empty_string(label):
                return None
            reference["label"] = label.strip()

        if source is not None:
            if not valid_non_empty_string(source):
                return None
            reference["source"] = source.strip()

        references.append(reference)

    return references


def explain_proposal(payload: object) -> dict[str, Any]:
    """Return a governed, deterministic proposal explanation."""

    if not isinstance(payload, dict):
        return error_response(
            "invalid_payload",
            "Proposal explanation requires a JSON payload object.",
        )

    proposal_id = payload.get("proposal_id")
    symbol = payload.get("symbol")
    side = payload.get("side")
    strategy = payload.get("strategy")
    estimated_notional = payload.get("estimated_notional")
    evidence = validate_evidence_references(payload.get("evidence_references"))

    if not valid_non_empty_string(proposal_id):
        return error_response("invalid_payload", "proposal_id is required.")

    if not valid_non_empty_string(symbol):
        return error_response("invalid_payload", "symbol is required.")

    if side not in {"BUY", "SELL"}:
        return error_response("invalid_payload", "side must be BUY or SELL.")

    if not valid_non_empty_string(strategy):
        return error_response("invalid_payload", "strategy is required.")

    if (
        isinstance(estimated_notional, bool)
        or not isinstance(estimated_notional, (int, float))
        or not math.isfinite(float(estimated_notional))
        or estimated_notional < 0
    ):
        return error_response(
            "invalid_payload",
            "estimated_notional must be a finite non-negative number.",
        )

    if evidence is None:
        return error_response(
            "invalid_payload",
            "evidence_references must be a list of valid evidence objects.",
        )

    normalized_proposal_id = proposal_id.strip()
    normalized_symbol = symbol.strip().upper()
    normalized_strategy = strategy.strip()
    normalized_notional = float(estimated_notional)

    return {
        "ok": True,
        "result": {
            "kind": "proposal-explanation",
            "summary": (
                f"Proposal {normalized_proposal_id} is governed, locally verified, and simulated."
            ),
            "explanation": (
                f"{normalized_symbol} is presented as a {side} proposal from "
                f"{normalized_strategy} with an estimated notional of "
                f"${normalized_notional:.2f}. The Python bridge validated the "
                "proposal context and evidence references. The proposal may be "
                "reviewed or simulated, but it cannot be submitted to a broker."
            ),
            "model_route": f"python-bridge-v{BRIDGE_VERSION}",
            "source": "local",
            "confidence": 1.0,
            "evidence_references": evidence,
            "generated_at": generated_at(),
            "execution_authorized": False,
            "broker_submission_available": False,
        },
    }


def handle_request(request: object) -> dict[str, Any]:
    """Handle one allow-listed bridge request."""

    if not isinstance(request, dict):
        return error_response(
            "invalid_request",
            "Request must be a JSON object.",
        )

    command = request.get("command")

    if command == "health":
        return {
            "ok": True,
            "result": backend_status(),
        }

    if command == "explain_proposal":
        return explain_proposal(request.get("payload"))

    if command == "runtime_snapshot":
        return {"ok": True, "result": runtime_snapshot()}

    if command == "control_paper_cycle":
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return error_response("invalid_payload", "Paper control requires a payload object.")
        try:
            return {"ok": True, "result": control_paper_cycle(payload.get("action"))}
        except ValueError as error:
            return error_response("paper_control_denied", str(error))

    if command == "control_paper_authorization":
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return error_response(
                "invalid_payload",
                "Paper authorization requires a payload object.",
            )
        try:
            return {
                "ok": True,
                "result": control_paper_authorization(payload.get("action")),
            }
        except ValueError as error:
            return error_response(
                "invalid_payload",
                str(error),
            )

    if command == "reset_paper_runtime":
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return error_response(
                "invalid_payload",
                "Paper reset requires a payload object.",
            )
        try:
            return {
                "ok": True,
                "result": reset_paper_runtime(payload.get("confirmation")),
            }
        except ValueError as error:
            return error_response("paper_reset_denied", str(error))

    if command == "governed_news_status":
        return {"ok": True, "result": governed_news_status()}

    if command == "governed_news_advisory_summary":
        return {"ok": True, "result": governed_news_advisory_summary()}

    if command == "governed_news_timeline":
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return error_response(
                "invalid_payload",
                "Governed news timeline requires a payload object.",
            )
        symbol = payload.get("symbol")
        if not valid_non_empty_string(symbol):
            return error_response(
                "invalid_payload",
                "Governed news timeline requires a symbol.",
            )
        return {"ok": True, "result": governed_news_timeline(symbol)}

    if command == "governed_alpaca_news_collect":
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return error_response(
                "invalid_payload",
                "Governed Alpaca news collection requires a payload object.",
            )
        symbols = payload.get("symbols")
        if (
            not isinstance(symbols, list)
            or not symbols
            or len(symbols) > 50
            or any(not valid_non_empty_string(symbol) for symbol in symbols)
        ):
            return error_response(
                "invalid_payload",
                "Governed Alpaca news symbols must contain 1 to 50 symbols.",
            )
        return {
            "ok": True,
            "result": governed_alpaca_news_collect([symbol.strip().upper() for symbol in symbols]),
        }

    if command == "provider_snapshot":
        return {"ok": True, "result": provider_snapshot()}

    if command == "market_universe_status":
        return {"ok": True, "result": market_universe_status()}

    if command == "market_universe_quotes":
        return {
            "ok": True,
            "result": market_universe_quotes(request.get("payload")),
        }

    if command == "market_universe_search":
        try:
            return {
                "ok": True,
                "result": market_universe_search(request.get("payload")),
            }
        except UniverseValidationError as error:
            return error_response("invalid_universe_query", str(error))

    if command == "alpaca_market_data_status":
        return {"ok": True, "result": alpaca_market_data_status()}

    if command == "control_alpaca_market_data":
        try:
            return {"ok": True, "result": control_alpaca_market_data(request.get("payload"))}
        except ValueError as error:
            return error_response("alpaca_market_data_control_denied", str(error))

    if command == "asset_catalog_status":
        return {"ok": True, "result": asset_catalog_status()}

    if command == "asset_catalog_refresh":
        return {"ok": True, "result": asset_catalog_refresh()}

    if command == "asset_catalog_snapshot":
        return {
            "ok": True,
            "result": asset_catalog_snapshot(request.get("payload")),
        }

    if command == "asset_catalog_statistics":
        return {"ok": True, "result": asset_catalog_statistics()}

    if command == "asset_catalog_sample":
        return {"ok": True, "result": asset_catalog_sample()}

    if command == "asset_catalog_exclusions":
        return {"ok": True, "result": asset_catalog_exclusions()}

    if command == "research_universe_status":
        return {"ok": True, "result": research_universe_status()}

    if command == "research_universe_advance":
        return {"ok": True, "result": research_universe_status(advance=True)}

    if command in {"paper_execution_status", "paper_policy_status"}:
        return {"ok": True, "result": paper_execution_status()}

    if command == "paper_execution_activate":
        return {"ok": True, "result": paper_execution_activate()}

    if command == "paper_execution_deactivate":
        return {"ok": True, "result": paper_execution_deactivate()}

    if command == "paper_execution_pause":
        return {"ok": True, "result": paper_execution_pause()}

    if command == "paper_execution_resume":
        return {"ok": True, "result": paper_execution_resume()}

    if command == "reconcile_paper_orders":
        return {"ok": True, "result": reconcile_paper_orders()}

    if command == "emergency_paper_stop":
        return {"ok": True, "result": paper_execution_emergency_stop()}

    if command in {
        "production_research_status",
        "strategy_status",
        "shadow_mode_status",
        "shadow_performance",
    }:
        return {"ok": True, "result": production_research_status()}

    if command == "paper_exit_status":
        return {"ok": True, "result": paper_execution_status()}

    if command == "promotion_readiness":
        return {"ok": True, "result": promotion_readiness()}

    if command == "shadow_mode_enable":
        return {"ok": True, "result": shadow_mode_enable()}

    if command == "shadow_mode_disable":
        return {"ok": True, "result": shadow_mode_disable()}

    if command == "request_paper_promotion":
        return {"ok": True, "result": request_paper_promotion()}

    research_collections = {
        "current_batch_research": "research",
        "recent_research_results": "research",
        "shadow_positions": "shadow_positions",
        "shadow_outcomes": "shadow_outcomes",
    }
    if command in research_collections:
        return {
            "ok": True,
            "result": production_research_collection(
                research_collections[command], request.get("payload")
            ),
        }

    research_details = {
        "candidate_detail": "candidates",
        "proposal_detail": "proposals",
    }
    if command in research_details:
        return {
            "ok": True,
            "result": production_research_detail(research_details[command], request.get("payload")),
        }

    if command == "reconcile_positions":
        return {"ok": True, "result": reconcile_positions()}

    if command == "emergency_paper_liquidation":
        return {"ok": True, "result": emergency_paper_liquidation()}

    if command == "position_detail":
        payload = request.get("payload")
        values = payload if isinstance(payload, dict) else {}
        identity = values.get("identity")
        positions = paper_execution_collection("positions", {"limit": 100})
        position = next(
            (item for item in positions["items"] if item.get("symbol") == identity),
            None,
        )
        if position is None:
            return error_response("paper_position_not_found", "Paper position was not found.")
        return {"ok": True, "result": {**positions, "item": position}}

    collection_commands = {
        "recent_candidates": "candidates",
        "recent_proposals": "proposals",
        "recent_rejections": "rejections",
        "paper_order_intents": "intents",
        "paper_orders": "orders",
        "paper_positions": "positions",
        "paper_fills": "fills",
    }
    if command in collection_commands:
        return {
            "ok": True,
            "result": paper_execution_collection(
                collection_commands[command], request.get("payload")
            ),
        }

    return error_response(
        "unsupported_command",
        "Only allow-listed local paper commands are available.",
    )


def main() -> int:
    """Read one JSON request from stdin and emit one JSON response."""

    try:
        request = json.load(sys.stdin)
        response = handle_request(request)
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = error_response(
            "invalid_json",
            "Input must contain one valid JSON object.",
        )
    except Exception:  # noqa: BLE001 - bridge boundary sanitizes every failure
        response = error_response(
            "bridge_failure",
            "The local Sigil bridge failed safely.",
        )

    json.dump(response, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
