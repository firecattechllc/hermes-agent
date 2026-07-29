"""Deterministic, paper-only execution primitives for the local runtime.

This module deliberately has no provider, credential, network, or broker
dependency.  It transforms a persisted runtime dictionary using only the
market snapshot supplied by its caller.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

MONEY = Decimal("0.01")
VALID_SIDES = frozenset({"BUY", "SELL"})
VALID_TYPES = frozenset({"MARKET", "LIMIT", "STOP"})


def money(value: Decimal) -> str:
    return str(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def number(value: object, field: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{field} must be a decimal number") from error
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "a positive " if positive else "a finite "
        raise ValueError(f"{field} must be {qualifier}decimal number")
    return result


def audit(state: dict[str, Any], *, timestamp: str, event: str, order_id: str, details: dict[str, Any]) -> None:
    sequence = len(state["audit"]) + 1
    state["audit"].insert(0, {
        "id": f"AUD-EXEC-{sequence:06d}", "timestamp": timestamp,
        "status": event, "proposal_id": "—", "order_id": order_id,
        "evidence_reference": f"PAPER-RUNTIME:{order_id}",
        "summary": f"Paper runtime {event.replace('_', ' ')}",
        "details": {**details, "paper_only": True, "broker_submission_attempted": False},
    })


def initialize_execution_state(state: dict[str, Any]) -> None:
    """Upgrade a Step 38 snapshot in-place without discarding its history."""
    balances = state.setdefault("balances", {})
    cash = Decimal(str(balances.get("cash", "10000.00")))
    balances.setdefault("cash", money(cash))
    balances.setdefault("reserved_cash", "0.00")
    balances.setdefault("buying_power", money(cash))
    balances.setdefault("equity", balances.get("portfolio_value", money(cash)))
    balances.setdefault("realized_pnl", "0.00")
    balances.setdefault("unrealized_pnl", "0.00")
    balances.setdefault("total_account_value", balances["equity"])
    for position in state.setdefault("positions", []):
        quantity = number(position.get("quantity", "0"), "position quantity")
        market_value = number(position.get("market_value", "0"), "position market value")
        average_cost = market_value / quantity if quantity else Decimal(0)
        position.setdefault("average_cost", str(average_cost))
        position.setdefault("market_value", money(market_value))
        position.setdefault("unrealized_pnl", "0.00")
        position.setdefault("realized_pnl", "0.00")
        position.setdefault("mark_status", "unavailable")
        position.setdefault("mark_price", None)
        position.setdefault("mark_timestamp", None)
        position.setdefault("mark_source", None)
        position.setdefault("mark_evidence_identity", None)
        position.setdefault("mark_error", "validated_position_mark_unavailable")
        position.setdefault("unrealized_pnl_status", "unavailable")
    state.setdefault("orders", {})
    state.setdefault("filled_orders", [])
    state.setdefault("cancelled_orders", [])
    state.setdefault("rejected_orders", [])
    state.setdefault("last_execution", None)
    state.setdefault("closed_positions", [])
    state.setdefault("runtime_health", "healthy")
    state["schema_version"] = max(int(state.get("schema_version", 1)), 2)


def evaluate_runtime_health(state: dict[str, Any]) -> str:
    """Derive a fail-closed health state from persisted paper-runtime facts."""
    initialize_execution_state(state)

    existing = state.get("runtime_health")
    if existing in {"corrupt", "locked"}:
        return str(existing)

    connection = state.get("connection", {})
    if connection.get("status") != "connected":
        state["runtime_health"] = "degraded"
        return "degraded"

    if any(
        item.get("required") is True
        for item in state.get("reconciliation", [])
        if isinstance(item, dict)
    ):
        state["runtime_health"] = "recovery_required"
        return "recovery_required"

    balances = state.get("balances")
    if not isinstance(balances, dict):
        state["runtime_health"] = "corrupt"
        return "corrupt"

    try:
        cash = Decimal(str(balances["cash"]))
        reserved_cash = Decimal(str(balances["reserved_cash"]))
        buying_power = Decimal(str(balances["buying_power"]))
    except (KeyError, ArithmeticError, ValueError):
        state["runtime_health"] = "corrupt"
        return "corrupt"

    if not all(value.is_finite() for value in (cash, reserved_cash, buying_power)):
        state["runtime_health"] = "corrupt"
        return "corrupt"

    if cash < 0 or reserved_cash < 0 or buying_power < 0:
        state["runtime_health"] = "corrupt"
        return "corrupt"

    if reserved_cash > cash:
        state["runtime_health"] = "degraded"
        return "degraded"

    expected_buying_power = (cash - reserved_cash).quantize(
        MONEY,
        rounding=ROUND_HALF_UP,
    )
    if buying_power.quantize(MONEY, rounding=ROUND_HALF_UP) != expected_buying_power:
        state["runtime_health"] = "degraded"
        return "degraded"

    orders = state.get("orders")
    if not isinstance(orders, dict):
        state["runtime_health"] = "corrupt"
        return "corrupt"

    allowed_statuses = {
        "open",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
    }

    for order_id, order in orders.items():
        if not isinstance(order_id, str) or not isinstance(order, dict):
            state["runtime_health"] = "corrupt"
            return "corrupt"

        status = order.get("status")
        if status not in allowed_statuses:
            state["runtime_health"] = "degraded"
            return "degraded"

        if status in {"open", "partially_filled"}:
            try:
                remaining = Decimal(str(order["remaining_quantity"]))
            except (KeyError, ArithmeticError, ValueError):
                state["runtime_health"] = "recovery_required"
                return "recovery_required"

            if not remaining.is_finite() or remaining <= 0:
                state["runtime_health"] = "recovery_required"
                return "recovery_required"

    state["runtime_health"] = "healthy"
    return "healthy"



def submit(state: dict[str, Any], request: dict[str, Any], *, timestamp: str) -> dict[str, Any]:
    """Validate and reserve an order.  It never contacts a broker."""
    initialize_execution_state(state)
    if request.get("environment", "paper") != "paper" or request.get("broker_submission") is True:
        raise ValueError("only local paper execution is permitted")
    order_id = str(request.get("order_id", "")).strip()
    symbol = str(request.get("symbol", "")).strip().upper()
    side = str(request.get("side", "")).strip().upper()
    order_type = str(request.get("order_type", "MARKET")).strip().upper()
    quantity = number(request.get("quantity"), "quantity", positive=True)
    if not order_id or not symbol or side not in VALID_SIDES or order_type not in VALID_TYPES:
        raise ValueError("order_id, symbol, side, and order_type are invalid")
    if order_id in state["orders"]:
        raise ValueError("order_id already exists")
    limit_price = request.get("limit_price")
    stop_price = request.get("stop_price")
    if order_type == "LIMIT" and limit_price is None:
        raise ValueError("limit orders require limit_price")
    if order_type == "STOP" and stop_price is None:
        raise ValueError("stop orders require stop_price")
    reserve_price = number(limit_price if limit_price is not None else request.get("reference_price"), "reference_price", positive=True)
    reserve = quantity * reserve_price if side == "BUY" else Decimal(0)
    if side == "SELL":
        position = next(
            (item for item in state["positions"] if item["symbol"] == symbol),
            None,
        )
        held = Decimal(position["quantity"]) if position is not None else Decimal(0)
        open_sell_quantity = sum(
            Decimal(existing["remaining_quantity"])
            for existing in state["orders"].values()
            if existing.get("symbol") == symbol
            and existing.get("side") == "SELL"
            and existing.get("status") in {"open", "partially_filled"}
        )
        if quantity > held - open_sell_quantity:
            order = {
                "id": order_id,
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "quantity": str(quantity),
                "filled_quantity": "0",
                "status": "rejected",
                "reason": "insufficient_position_quantity",
                "created_at": timestamp,
            }
            state["orders"][order_id] = order
            state["rejected_orders"].insert(0, order_id)
            audit(
                state,
                timestamp=timestamp,
                event="order_rejected",
                order_id=order_id,
                details={"reason": order["reason"]},
            )
            return deepcopy(order)
    if reserve > Decimal(state["balances"]["buying_power"]):
        order = {"id": order_id, "symbol": symbol, "side": side, "order_type": order_type, "quantity": str(quantity), "filled_quantity": "0", "status": "rejected", "reason": "insufficient_buying_power", "created_at": timestamp}
        state["orders"][order_id] = order
        state["rejected_orders"].insert(0, order_id)
        audit(state, timestamp=timestamp, event="order_rejected", order_id=order_id, details={"reason": order["reason"]})
        return deepcopy(order)
    order = {"id": order_id, "symbol": symbol, "side": side, "order_type": order_type, "quantity": str(quantity), "filled_quantity": "0", "remaining_quantity": str(quantity), "reference_price": str(reserve_price), "limit_price": str(limit_price) if limit_price is not None else None, "stop_price": str(stop_price) if stop_price is not None else None, "reserved_cash": money(reserve), "status": "open", "created_at": timestamp}
    state["orders"][order_id] = order
    state["balances"]["reserved_cash"] = money(Decimal(state["balances"]["reserved_cash"]) + reserve)
    state["balances"]["buying_power"] = money(Decimal(state["balances"]["buying_power"]) - reserve)
    audit(state, timestamp=timestamp, event="order_submitted", order_id=order_id, details={"reserved_cash": money(reserve)})
    return deepcopy(order)


def _fillable(order: dict[str, Any], price: Decimal) -> bool:
    if order["order_type"] == "MARKET":
        return True
    if order["order_type"] == "LIMIT":
        return price <= Decimal(order["limit_price"]) if order["side"] == "BUY" else price >= Decimal(order["limit_price"])
    return False  # Stop orders are retained as state only.


def fill(state: dict[str, Any], order_id: str, snapshot: dict[str, Any], *, timestamp: str, quantity: object | None = None) -> dict[str, Any]:
    """Apply an injected deterministic market price to an open paper order."""
    initialize_execution_state(state)
    order = state["orders"].get(order_id)
    if not order or order["status"] not in {"open", "partially_filled"}:
        raise ValueError("order is not open")
    price = number(snapshot.get(order["symbol"]), "market snapshot price", positive=True)
    if not _fillable(order, price):
        return deepcopy(order)
    remaining = Decimal(order["remaining_quantity"])
    fill_quantity = remaining if quantity is None else number(quantity, "fill quantity", positive=True)
    if fill_quantity > remaining:
        raise ValueError("fill quantity exceeds remaining quantity")
    cost = fill_quantity * price
    positions = {item["symbol"]: item for item in state["positions"]}
    position = positions.get(order["symbol"])
    if order["side"] == "BUY":
        if position is None:
            position = {"symbol": order["symbol"], "quantity": "0", "average_cost": "0", "market_value": "0", "unrealized_pnl": "0", "realized_pnl": "0"}
            state["positions"].append(position)
        old_qty, old_cost = Decimal(position["quantity"]), Decimal(position["average_cost"])
        position["average_cost"] = str((old_qty * old_cost + cost) / (old_qty + fill_quantity))
        position["quantity"] = str(old_qty + fill_quantity)
        state["balances"]["cash"] = money(Decimal(state["balances"]["cash"]) - cost)
    else:
        if position is None or Decimal(position["quantity"]) < fill_quantity:
            raise ValueError("insufficient position quantity")
        realized = (price - Decimal(position["average_cost"])) * fill_quantity
        position["quantity"] = str(Decimal(position["quantity"]) - fill_quantity)
        position["realized_pnl"] = money(Decimal(position["realized_pnl"]) + realized)
        state["balances"]["realized_pnl"] = money(Decimal(state["balances"]["realized_pnl"]) + realized)
        state["balances"]["cash"] = money(Decimal(state["balances"]["cash"]) + cost)
    reserved_release = min(
        Decimal(order["reserved_cash"]),
        fill_quantity * Decimal(order["reference_price"]),
    )
    state["balances"]["reserved_cash"] = money(Decimal(state["balances"]["reserved_cash"]) - reserved_release)
    order["reserved_cash"] = money(Decimal(order["reserved_cash"]) - reserved_release)
    order["filled_quantity"] = str(Decimal(order["filled_quantity"]) + fill_quantity)
    order["remaining_quantity"] = str(remaining - fill_quantity)
    order["status"] = "filled" if remaining == fill_quantity else "partially_filled"
    receipt = {"id": f"PAPER-FILL-{order_id}-{len(state['filled_orders']) + 1:04d}", "order_id": order_id, "symbol": order["symbol"], "side": order["side"], "quantity": str(fill_quantity), "price": money(price), "timestamp": timestamp, "status": order["status"], "broker_submission_attempted": False}
    state["filled_orders"].insert(0, receipt)
    state["executions"].insert(0, receipt)
    state["last_execution"] = receipt
    audit(state, timestamp=timestamp, event="order_filled", order_id=order_id, details={"quantity": str(fill_quantity), "price": money(price), "partial": order["status"] == "partially_filled"})
    recalculate(state, snapshot)
    return deepcopy(order)


def cancel(state: dict[str, Any], order_id: str, *, timestamp: str) -> dict[str, Any]:
    initialize_execution_state(state)
    order = state["orders"].get(order_id)
    if not order or order["status"] not in {"open", "partially_filled"}:
        raise ValueError("order is not cancellable")
    release = Decimal(order["reserved_cash"])
    state["balances"]["reserved_cash"] = money(Decimal(state["balances"]["reserved_cash"]) - release)
    state["balances"]["buying_power"] = money(Decimal(state["balances"]["buying_power"]) + release)
    order.update({"reserved_cash": "0.00", "status": "cancelled", "cancelled_at": timestamp})
    state["cancelled_orders"].insert(0, order_id)
    audit(state, timestamp=timestamp, event="order_cancelled", order_id=order_id, details={})
    return deepcopy(order)


def recalculate(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    market_value = Decimal(0)
    unrealized = Decimal(0)
    for position in state["positions"]:
        quantity = Decimal(position["quantity"])
        price = number(snapshot.get(position["symbol"], position.get("average_cost")), "market snapshot price", positive=True)
        value = quantity * price
        pnl = (price - Decimal(position["average_cost"])) * quantity
        position["market_value"] = money(value)
        position["unrealized_pnl"] = money(pnl)
        market_value += value
        unrealized += pnl
    cash = Decimal(state["balances"]["cash"])
    equity = cash + market_value
    state["balances"].update({"portfolio_value": money(market_value), "equity": money(equity), "unrealized_pnl": money(unrealized), "total_account_value": money(equity), "buying_power": money(cash - Decimal(state["balances"]["reserved_cash"]))})


def apply_position_marks(
    state: dict[str, Any],
    marks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Persist validated local marks and recalculate only from fresh evidence."""
    initialize_execution_state(state)
    snapshot: dict[str, str] = {}
    unavailable: list[str] = []
    for position in state["positions"]:
        if Decimal(str(position.get("quantity", "0"))) <= 0:
            continue
        symbol = str(position["symbol"])
        mark = marks.get(symbol)
        status = str(mark.get("status")) if isinstance(mark, dict) else "unavailable"
        position["mark_status"] = status
        position["mark_error"] = (
            None
            if status == "fresh"
            else str(
                mark.get("reason", "validated_position_mark_unavailable")
                if isinstance(mark, dict)
                else "validated_position_mark_unavailable"
            )
        )
        if isinstance(mark, dict):
            position["mark_timestamp"] = mark.get("timestamp")
            position["mark_source"] = mark.get("source")
            position["mark_evidence_identity"] = mark.get("evidence_identity")
        if status != "fresh" or not isinstance(mark, dict):
            position["unrealized_pnl_status"] = status
            unavailable.append(symbol)
            continue
        price = number(mark.get("price"), "position mark", positive=True)
        position["mark_price"] = str(price)
        position["unrealized_pnl_status"] = "fresh"
        snapshot[symbol] = str(price)

    active = [
        position
        for position in state["positions"]
        if Decimal(str(position.get("quantity", "0"))) > 0
    ]
    if active and unavailable:
        state["balances"]["valuation_status"] = "unavailable"
        state["balances"]["unrealized_pnl_status"] = "unavailable"
    else:
        recalculate(state, snapshot)
        state["balances"]["valuation_status"] = "fresh"
        state["balances"]["unrealized_pnl_status"] = "fresh"
    return {
        "status": "fresh" if not unavailable else "unavailable",
        "fresh_symbols": sorted(snapshot),
        "unavailable_symbols": sorted(unavailable),
    }


def mission_control_status(state: dict[str, Any]) -> dict[str, Any]:
    initialize_execution_state(state)
    health = evaluate_runtime_health(state)
    return {
        "paper_mode": True,
        "broker_execution_disabled": True,
        "runtime_health": health,
        "cash": state["balances"]["cash"],
        "positions": deepcopy(state["positions"]),
        "open_orders": [
            deepcopy(order)
            for order in state["orders"].values()
            if order["status"] in {"open", "partially_filled"}
        ],
        "last_execution": deepcopy(state["last_execution"]),
    }
