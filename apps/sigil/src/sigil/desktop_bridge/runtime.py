"""Durable, governed paper-trading runtime for the local Sigil desktop bridge."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .paper_execution import (
    cancel as _cancel_order,
)
from .paper_execution import (
    evaluate_runtime_health,
    initialize_execution_state,
    mission_control_status,
)
from .paper_execution import (
    fill as _fill_order,
)
from .paper_execution import (
    recalculate as _recalculate,
)
from .paper_execution import (
    submit as _submit_order,
)

SCHEMA_VERSION = 5
CYCLE_SECONDS = 5
CONTROL_ACTIONS = frozenset({"start", "pause", "stop"})
AUTHORIZATION_ACTIONS = frozenset({"grant", "revoke"})


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _authorization_month(value: datetime) -> str:
    return value.strftime("%Y-%m")


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(
            year=value.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return value.replace(month=value.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _monthly_authorization(now: datetime) -> dict[str, Any]:
    month = _authorization_month(now)
    return {
        "status": "active",
        "authorization_id": f"PAPER-AUTH-{month}-AUTO",
        "authorization_month": month,
        "authorized_at": _timestamp(now),
        "expires_at": _timestamp(_next_month(now)),
        "revoked_at": None,
        "scope": [
            "automatic-paper-approval",
            "simulated-paper-buy",
            "simulated-paper-sell",
        ],
        "automatic_monthly_policy": True,
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _state_directory() -> Path:
    raw = os.environ.get("SIGIL_DESKTOP_STATE_DIR")
    if not raw:
        raise RuntimeError("SIGIL_DESKTOP_STATE_DIR is required")
    directory = Path(raw)
    if not directory.is_absolute():
        raise RuntimeError("SIGIL_DESKTOP_STATE_DIR must be absolute")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise RuntimeError("SIGIL_DESKTOP_STATE_DIR cannot be a symlink")
    return directory


def _initial_state(now: datetime) -> dict[str, Any]:
    timestamp = _timestamp(now)
    authorization = _monthly_authorization(now)
    state = {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "generated_at": timestamp,
        "connection": {
            "status": "connected",
            "last_refresh_at": timestamp,
            "degraded_services": [],
        },
        "environment": "paper",
        "simulation": True,
        "execution_authorized": False,
        "broker_submission_available": False,
        "balances": {"cash": "10000.00", "portfolio_value": "10842.16", "currency": "USD"},
        "positions": [
            {"symbol": "MSFT", "quantity": "1.25", "market_value": "566.00"},
            {"symbol": "NVDA", "quantity": "1.60", "market_value": "276.80"},
        ],
        "automation": {
            "state": "stopped",
            "cycle_count": 0,
            "last_cycle_at": None,
            "next_cycle_at": None,
            "mode": "monthly-authorized-paper-execution",
            "pause_cause": None,
            "pause_reason": None,
            "cycle_execution_id": None,
            "cycle_started_at": None,
            "cycle_status": "idle",
            "last_cycle_status": None,
        },
        "paper_authorization": authorization,
        "proposals": [],
        "executions": [],
        "reconciliation": [],
        "audit": [
            {
                "id": "AUD-RUNTIME-0001",
                "timestamp": timestamp,
                "status": "monthly_authorization_started",
                "proposal_id": "—",
                "order_id": "—",
                "evidence_reference": authorization["authorization_id"],
                "summary": "Calendar-month paper authorization started automatically",
                "details": {
                    "authorization_month": authorization["authorization_month"],
                    "automatic_monthly_policy": True,
                    "broker_submission_attempted": False,
                    "paper_only": True,
                },
            }
        ],
    }
    initialize_execution_state(state)
    _recalculate(
        state,
        {"MSFT": "452.80", "NVDA": "173.00"},
    )
    return state


def _empty_reset_state(
    now: datetime, *, reset_id: str, previous_state_sha256: str
) -> dict[str, Any]:
    state = _initial_state(now)
    state["positions"] = []
    state["proposals"] = []
    state["executions"] = []
    state["reconciliation"] = []
    state["orders"] = {}
    state["filled_orders"] = []
    state["cancelled_orders"] = []
    state["rejected_orders"] = []
    state["last_execution"] = None
    state["balances"].update(
        {
            "cash": "10000.00",
            "reserved_cash": "0.00",
            "buying_power": "10000.00",
            "portfolio_value": "0.00",
            "equity": "10000.00",
            "realized_pnl": "0.00",
            "unrealized_pnl": "0.00",
            "total_account_value": "10000.00",
        }
    )
    state["audit"] = [
        {
            "id": "AUD-RUNTIME-RESET-0001",
            "timestamp": _timestamp(now),
            "status": "paper_runtime_reset",
            "proposal_id": "—",
            "order_id": "—",
            "evidence_reference": reset_id,
            "summary": "Local paper portfolio reset to an empty governed ledger",
            "details": {
                "paper_only": True,
                "previous_state_sha256": previous_state_sha256,
                "settings_preserved": True,
                "credentials_preserved": True,
                "broker_submission_attempted": False,
            },
        }
    ]
    return state


def _append_reset_evidence(
    directory: Path, state: dict[str, Any], now: datetime
) -> tuple[str, str]:
    evidence_path = directory / "runtime-reset-audit.jsonl"
    if evidence_path.is_symlink():
        raise RuntimeError("paper reset audit cannot be a symlink")
    previous_record_sha256 = "0" * 64
    if evidence_path.exists():
        for line in evidence_path.read_text(encoding="utf-8").splitlines():
            envelope = json.loads(line)
            body = {
                "record": envelope.get("record"),
                "previous_record_sha256": envelope.get("previous_record_sha256"),
            }
            if envelope.get("previous_record_sha256") != previous_record_sha256 or envelope.get(
                "sha256"
            ) != _digest(body):
                raise RuntimeError("paper reset audit integrity validation failed")
            previous_record_sha256 = str(envelope["sha256"])
    previous_state_sha256 = _digest(state)
    reset_id = f"PAPER-RESET-{now.strftime('%Y%m%dT%H%M%SZ')}"
    record = {
        "reset_id": reset_id,
        "timestamp": _timestamp(now),
        "scope": "local-paper-runtime-only",
        "previous_state_sha256": previous_state_sha256,
        "settings_preserved": True,
        "credentials_preserved": True,
        "provider_mutation": False,
        "broker_submission_attempted": False,
    }
    body = {
        "record": record,
        "previous_record_sha256": previous_record_sha256,
    }
    envelope = {**body, "sha256": _digest(body)}
    with evidence_path.open("a", encoding="utf-8") as output:
        os.chmod(evidence_path, 0o600)
        output.write(_canonical(envelope).decode())
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return reset_id, previous_state_sha256


def _upgrade_runtime_state(state: dict[str, Any]) -> None:
    initialize_execution_state(state)
    state["schema_version"] = SCHEMA_VERSION
    automation = state.setdefault("automation", {})
    automation.pop("proposal_only", None)
    automation.setdefault("mode", "monthly-authorized-paper-execution")
    automation.setdefault("pause_cause", None)
    automation.setdefault("pause_reason", None)
    automation.setdefault("cycle_execution_id", None)
    automation.setdefault("cycle_started_at", None)
    automation.setdefault("cycle_status", "idle")
    automation.setdefault("last_cycle_status", None)
    state.setdefault(
        "paper_authorization",
        {
            "status": "required",
            "authorization_id": None,
            "authorized_at": None,
            "expires_at": None,
            "revoked_at": None,
            "scope": [],
        },
    )


def _ensure_month_authorization(state: dict[str, Any], now: datetime) -> None:
    authorization = state["paper_authorization"]
    month = _authorization_month(now)
    if authorization.get("authorization_month") == month:
        return
    authorization.update(_monthly_authorization(now))
    state["audit"].insert(
        0,
        {
            "id": f"AUD-AUTH-MONTH-{month}",
            "timestamp": _timestamp(now),
            "status": "monthly_authorization_started",
            "proposal_id": "—",
            "order_id": "—",
            "evidence_reference": authorization["authorization_id"],
            "summary": "Calendar-month paper authorization started automatically",
            "details": {
                "authorization_month": month,
                "automatic_monthly_policy": True,
                "paper_only": True,
                "scope": authorization["scope"],
                "broker_submission_attempted": False,
            },
        },
    )


def _recover_invalid_runtime_state(
    state_path: Path,
    directory: Path,
    *,
    reason: str,
) -> dict[str, Any]:
    """Quarantine invalid local paper state and safely create a clean runtime."""

    now = _now()
    quarantine_name = f"runtime-state.invalid-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    quarantine_path = directory / quarantine_name

    if state_path.exists():
        if state_path.is_symlink():
            raise RuntimeError("paper runtime state cannot be a symlink")
        os.replace(state_path, quarantine_path)
        os.chmod(quarantine_path, 0o600)

    state = _initial_state(now)
    recovery_id = f"PAPER-RECOVERY-{now.strftime('%Y%m%dT%H%M%SZ')}"

    state["audit"].insert(
        0,
        {
            "id": "AUD-RUNTIME-RECOVERY-0001",
            "timestamp": _timestamp(now),
            "status": "paper_runtime_recovered",
            "proposal_id": "—",
            "order_id": "—",
            "evidence_reference": recovery_id,
            "summary": (
                "Invalid local paper runtime state was quarantined and "
                "replaced with a clean governed runtime"
            ),
            "details": {
                "paper_only": True,
                "broker_submission_attempted": False,
                "recovery_reason": reason,
                "quarantined_state_file": (
                    quarantine_path.name if quarantine_path.exists() else None
                ),
            },
        },
    )

    return state


@contextmanager
def _locked_state() -> Iterator[tuple[Path, dict[str, Any]]]:
    directory = _state_directory()
    state_path = directory / "runtime-state.json"
    lock_path = directory / "runtime-state.lock"
    if state_path.is_symlink() or lock_path.is_symlink():
        raise RuntimeError("paper runtime state files cannot be symlinks")
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if state_path.exists():
            try:
                envelope = json.loads(state_path.read_text(encoding="utf-8"))
                payload = envelope.get("payload")
                valid = (
                    isinstance(payload, dict)
                    and envelope.get("sha256") == _digest(payload)
                    and payload.get("schema_version") in {1, 2, 3, 4, SCHEMA_VERSION}
                )
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                payload = None
                valid = False

            if valid:
                _upgrade_runtime_state(payload)
            else:
                payload = _recover_invalid_runtime_state(
                    state_path,
                    directory,
                    reason="paper runtime state integrity validation failed",
                )
        else:
            payload = _initial_state(_now())
        yield state_path, payload


def _persist(state_path: Path, payload: dict[str, Any]) -> None:
    envelope = {"payload": payload, "sha256": _digest(payload)}
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-state.", dir=state_path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(_canonical(envelope))
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, state_path)
        directory_fd = os.open(state_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _clear_cycle_claim(
    automation: dict[str, Any],
    *,
    last_status: str | None = None,
) -> None:
    automation["cycle_execution_id"] = None
    automation["cycle_started_at"] = None
    automation["cycle_status"] = "idle"
    if last_status is not None:
        automation["last_cycle_status"] = last_status


def _recover_interrupted_cycle(
    state: dict[str, Any],
    now: datetime,
) -> bool:
    """Recover a persisted unfinished cycle and pause fail-closed."""

    automation = state["automation"]
    execution_id = automation.get("cycle_execution_id")
    if not execution_id:
        return False

    started_at = automation.get("cycle_started_at")

    automation.update(
        {
            "state": "paused",
            "next_cycle_at": None,
            "pause_cause": "safety",
            "pause_reason": ("An interrupted paper cycle was recovered; manual resume is required"),
        }
    )
    _clear_cycle_claim(
        automation,
        last_status="interrupted_recovered",
    )

    state["audit"].insert(
        0,
        {
            "id": (f"AUD-CYCLE-RECOVERY-{int(state['revision']) + 1:06d}"),
            "timestamp": _timestamp(now),
            "status": "paper_cycle_interrupted_recovered",
            "proposal_id": "—",
            "order_id": "—",
            "evidence_reference": str(execution_id),
            "summary": ("Interrupted paper cycle recovered; automation paused fail-closed"),
            "details": {
                "paper_only": True,
                "execution_id": str(execution_id),
                "cycle_started_at": started_at,
                "requires_manual_resume": True,
                "broker_submission_attempted": False,
            },
        },
    )
    return True


def _authorization_active(state: dict[str, Any], now: datetime) -> bool:
    _ensure_month_authorization(state, now)
    authorization = state["paper_authorization"]
    expires_at = authorization.get("expires_at")
    if authorization.get("status") != "active" or not expires_at:
        if state["automation"].get("state") == "running":
            state["automation"].update({"state": "paused", "next_cycle_at": None})
        return False
    if datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= now:
        authorization["status"] = "expired"
        state["automation"].update({"state": "paused", "next_cycle_at": None})
        state["audit"].insert(
            0,
            {
                "id": f"AUD-AUTH-{int(state['revision']) + 1:06d}",
                "timestamp": _timestamp(now),
                "status": "authorization_expired",
                "proposal_id": "—",
                "order_id": "—",
                "evidence_reference": str(
                    authorization.get("authorization_id") or "PAPER-AUTHORIZATION"
                ),
                "summary": "Monthly paper authorization expired; automation paused",
                "details": {
                    "paper_only": True,
                    "broker_submission_attempted": False,
                    "authorization_active": False,
                },
            },
        )
        return False
    return True


def _cycle_order(
    state: dict[str, Any], sequence: int
) -> tuple[str, str, Decimal, Decimal, Decimal]:
    if os.environ.get("SIGIL_ASSET_CATALOG_MODE") != "demo":
        raise ValueError("catalog research completed without validated proposal market data")
    from .universe import (
        PAPER_SIMULATION_PRICES,
        US_LISTED_SCREENING_UNIVERSE,
    )

    market_prices = {symbol: Decimal(price) for symbol, price in PAPER_SIMULATION_PRICES.items()}
    side = "BUY" if sequence % 2 else "SELL"
    symbols = tuple(item["symbol"] for item in US_LISTED_SCREENING_UNIVERSE)
    symbol = symbols[(sequence - 1) % len(symbols)]
    price = market_prices[symbol]
    if side == "BUY":
        buying_power = Decimal(state["balances"]["buying_power"])
        notional_budget = min(buying_power * Decimal("0.05"), buying_power)
        quantity = (notional_budget / price).quantize(Decimal("0.0001"))
        if quantity <= 0:
            raise ValueError("paper buying power is exhausted")
    else:
        position = next(
            (
                item
                for item in state["positions"]
                if item["symbol"] == symbol and Decimal(item["quantity"]) > 0
            ),
            None,
        )
        if position is None:
            position = next(
                (item for item in state["positions"] if Decimal(item["quantity"]) > 0),
                None,
            )
        if position is None:
            raise ValueError("no simulated position is available to sell")
        symbol = str(position["symbol"])
        price = market_prices[symbol]
        held = Decimal(position["quantity"])
        quantity = max(
            min(held * Decimal("0.10"), held).quantize(Decimal("0.0001")),
            min(held, Decimal("0.0001")),
        )
    return symbol, side, quantity, price, quantity * price


def _run_due_cycle(
    state_path: Path,
    state: dict[str, Any],
    now: datetime,
) -> None:
    automation = state["automation"]
    health = evaluate_runtime_health(state)
    if health != "healthy":
        was_running = automation.get("state") == "running"
        automation["state"] = "paused"
        automation["next_cycle_at"] = None
        automation["pause_cause"] = "safety"
        automation["pause_reason"] = f"Runtime health is {health}"
        if was_running:
            state["audit"].insert(
                0,
                {
                    "id": f"AUD-SAFETY-{int(state['revision']) + 1:06d}",
                    "timestamp": _timestamp(now),
                    "status": "safety_paused",
                    "proposal_id": "—",
                    "order_id": "—",
                    "evidence_reference": "RUNTIME-HEALTH",
                    "summary": f"Paper automation paused by safety condition: {health}",
                    "details": {
                        "paper_only": True,
                        "runtime_health": health,
                        "requires_manual_resume": True,
                        "broker_submission_attempted": False,
                    },
                },
            )
        return
    if automation["state"] != "running":
        return
    if not _authorization_active(state, now):
        return
    next_cycle = automation.get("next_cycle_at")
    if next_cycle and datetime.fromisoformat(next_cycle.replace("Z", "+00:00")) > now:
        return

    if automation.get("cycle_execution_id"):
        return

    sequence = int(automation["cycle_count"]) + 1
    timestamp = _timestamp(now)
    execution_id = f"PAPER-CYCLE-{sequence:06d}-{now.strftime('%Y%m%dT%H%M%SZ')}"

    automation.update(
        {
            "cycle_execution_id": execution_id,
            "cycle_started_at": timestamp,
            "cycle_status": "running",
        }
    )

    # Persist the claim before proposals, orders, or fills are created.
    _persist(state_path, state)

    if os.environ.get("SIGIL_ASSET_CATALOG_MODE") != "demo":
        from .asset_catalog import research_universe_status

        research = research_universe_status(advance=True)
        if research.get("revision") == "catalog-unavailable":
            automation.update(
                {
                    "state": "paused",
                    "next_cycle_at": None,
                    "pause_cause": "safety",
                    "pause_reason": (
                        "Governed Alpaca asset catalog is unavailable; "
                        "catalog-dependent research suspended"
                    ),
                }
            )
            _clear_cycle_claim(automation, last_status="catalog_unavailable")
            state["audit"].insert(
                0,
                {
                    "id": f"AUD-CATALOG-{sequence:06d}",
                    "timestamp": timestamp,
                    "status": "catalog_research_suspended",
                    "proposal_id": "—",
                    "order_id": "—",
                    "evidence_reference": "CATALOG-UNAVAILABLE",
                    "summary": ("Catalog-dependent research suspended fail-closed"),
                    "details": {
                        "paper_only": True,
                        "broker_submission_attempted": False,
                    },
                },
            )
            return
        automation.update(
            {
                "cycle_count": sequence,
                "last_cycle_at": timestamp,
                "next_cycle_at": _timestamp(
                    now.replace(microsecond=0) + timedelta(seconds=CYCLE_SECONDS)
                ),
            }
        )
        from .production_research import run_production_batch

        production = run_production_batch(
            list(research.get("symbols", [])),
            cursor=int(research.get("next_cursor", 0)),
            batch_number=int(research.get("current_batch", 0)),
            total_eligible=int(research.get("proposal_eligible", 0)),
            next_cycle_at=automation["next_cycle_at"],
            now=now,
        )
        _clear_cycle_claim(automation, last_status="research_batch_completed")
        state["audit"].insert(
            0,
            {
                "id": f"AUD-RESEARCH-{sequence:06d}",
                "timestamp": timestamp,
                "status": "catalog_research_batch_completed",
                "proposal_id": "—",
                "order_id": "—",
                "evidence_reference": str(research["revision"]),
                "summary": (
                    "Governed catalog batch completed production research "
                    f"with state {production['progress']['state']}"
                ),
                "details": {
                    "paper_only": True,
                    "symbols_examined": research.get("symbols", []),
                    "batch_size": research.get("batch_size"),
                    "next_cursor": research.get("next_cursor"),
                    "broker_submission_attempted": production["broker_submission_attempted"],
                    "proposal_created": (production["progress"]["proposals_generated"] > 0),
                    "candidate_scoring_completed": True,
                    "strategy_id": production["strategy_id"],
                    "strategy_version": production["strategy_version"],
                    "shadow_mode": production["shadow_mode"],
                    "leading_rejection_reasons": production["progress"][
                        "leading_rejection_reasons"
                    ],
                },
            },
        )
        return

    proposal_id = f"PRP-PAPER-{sequence:06d}"
    evidence_id = f"HERMES-PAPER-{sequence:06d}"
    symbol, side, quantity, price, notional = _cycle_order(state, sequence)
    authorization_id = state["paper_authorization"]["authorization_id"]
    state["proposals"].insert(
        0,
        {
            "id": proposal_id,
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "estimated_notional": f"{notional:.2f}",
            "strategy": "Monthly-authorized governed paper automation",
            "status": "approved",
            "approval": {
                "mode": "automatic-paper-only",
                "authorization_id": authorization_id,
                "approved_at": timestamp,
            },
            "evidence_references": [evidence_id],
            "risk_results": [
                "Monthly paper authorization active",
                (
                    "Dynamic allocation: at most 5% of available paper buying power"
                    if side == "BUY"
                    else "Dynamic allocation: at most 10% of simulated holdings"
                ),
                "Broker execution disabled",
            ],
        },
    )
    order_id = f"PAPER-ORD-{sequence:06d}"
    state["audit"].insert(
        0,
        {
            "id": f"AUD-APPROVAL-{sequence:06d}",
            "timestamp": timestamp,
            "status": "paper_auto_approved",
            "proposal_id": proposal_id,
            "order_id": order_id,
            "evidence_reference": str(authorization_id),
            "summary": "Paper proposal approved automatically under active monthly authorization",
            "details": {
                "paper_only": True,
                "simulated": True,
                "side": side,
                "symbol": symbol,
                "quantity": str(quantity),
                "estimated_notional": f"{notional:.2f}",
                "authorization_id": authorization_id,
                "broker_submission_attempted": False,
            },
        },
    )
    order = _submit_order(
        state,
        {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "order_type": "MARKET",
            "quantity": str(quantity),
            "reference_price": str(price),
            "environment": "paper",
            "broker_submission": False,
        },
        timestamp=timestamp,
    )
    if order["status"] != "open":
        raise RuntimeError(f"authorized paper order rejected: {order.get('reason')}")
    _fill_order(
        state,
        order_id,
        {symbol: str(price)},
        timestamp=timestamp,
    )
    state["reconciliation"].insert(
        0,
        {
            "order_id": order_id,
            "status": "reconciled-local-paper",
            "required": False,
            "automatic_retry_allowed": False,
            "timestamp": timestamp,
            "evidence_reference": f"PAPER-RUNTIME:{order_id}",
        },
    )
    state["audit"].insert(
        0,
        {
            "id": f"AUD-PAPER-{sequence:06d}",
            "timestamp": timestamp,
            "status": "paper_executed",
            "proposal_id": proposal_id,
            "order_id": order_id,
            "evidence_reference": evidence_id,
            "summary": f"Governed paper {side.lower()} simulated and reconciled",
            "details": {
                "paper_only": True,
                "approval_created": True,
                "approval_mode": "automatic-paper-only",
                "local_paper_fill": True,
                "side": side,
                "symbol": symbol,
                "quantity": str(quantity),
                "price": f"{price:.2f}",
                "notional": f"{notional:.2f}",
                "authorization_id": authorization_id,
                "cycle_execution_id": execution_id,
                "broker_submission_attempted": False,
            },
        },
    )
    automation.update(
        {
            "cycle_count": sequence,
            "last_cycle_at": timestamp,
            "next_cycle_at": _timestamp(
                now.replace(microsecond=0) + timedelta(seconds=CYCLE_SECONDS)
            ),
            "pause_cause": None,
            "pause_reason": None,
            "cycle_execution_id": None,
            "cycle_started_at": None,
            "cycle_status": "idle",
            "last_cycle_status": "completed",
        }
    )


def _runtime_visibility(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Project governed runtime state without creating execution authority."""
    automation = state["automation"]
    raw_health = str(state.get("runtime_health", "corrupt"))
    health = (
        "healthy"
        if raw_health == "healthy"
        else "degraded"
        if raw_health == "degraded"
        else "blocked"
    )
    authorization = state["paper_authorization"]
    authorization_status = str(authorization.get("status", "required"))
    expires_at = authorization.get("expires_at")
    authorization_active = authorization_status == "active"
    if authorization_active and isinstance(expires_at, str):
        authorization_active = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) > now

    reasons: list[dict[str, object]] = []

    def reason(
        code: str,
        severity: str,
        summary: str,
        *,
        requires_manual_resume: bool = False,
    ) -> None:
        reasons.append(
            {
                "code": code,
                "severity": severity,
                "summary": summary,
                "requires_manual_resume": requires_manual_resume,
            }
        )

    automation_state = str(automation.get("state", "stopped"))
    if automation_state == "paused":
        if automation.get("pause_cause") == "safety":
            reason(
                "automation_safety_paused",
                "critical" if health == "blocked" else "warning",
                str(
                    automation.get("pause_reason")
                    or "Automation was paused by a runtime safety condition"
                ),
                requires_manual_resume=True,
            )
        else:
            reason(
                "automation_paused",
                "warning",
                "Automation is paused by the owner",
                requires_manual_resume=True,
            )
    elif automation_state == "stopped":
        reason(
            "automation_stopped",
            "warning",
            "Automation is stopped",
            requires_manual_resume=True,
        )

    if not authorization_active:
        reason(
            f"authorization_{authorization_status}",
            "critical",
            f"Paper authorization is {authorization_status}",
        )
    if health != "healthy":
        reason(
            f"runtime_health_{raw_health}",
            "critical" if health == "blocked" else "warning",
            f"Runtime health is {raw_health.replace('_', ' ')}",
        )
    connection = state.get("connection", {})
    if connection.get("status") != "connected" or connection.get("degraded_services"):
        reason(
            "services_degraded",
            "warning",
            "Connection or required services are degraded",
        )
    if not state.get("execution_authorized", False):
        reason(
            "execution_authorization_false",
            "info",
            "Real broker execution authorization is disabled",
        )
    if not state.get("broker_submission_available", False):
        reason(
            "broker_submission_unavailable",
            "info",
            "Real broker submission is unavailable; local paper simulation remains separate",
        )

    critical_block = any(item["severity"] == "critical" for item in reasons)
    operational_state = automation_state
    if automation_state == "running" and critical_block:
        operational_state = "blocked"

    paper_execution_available = (
        state.get("environment") == "paper"
        and state.get("simulation") is True
        and authorization_active
        and health == "healthy"
    )
    if operational_state == "running":
        next_action = "Run the next governed local paper cycle when scheduled"
    elif automation.get("pause_cause") == "safety":
        next_action = "Resolve the safety condition, then explicitly resume automation"
    elif operational_state == "paused":
        next_action = "Explicitly resume local paper automation"
    elif operational_state == "stopped":
        next_action = "Explicitly start local paper automation"
    else:
        next_action = "Resolve critical blocking reasons"

    return {
        "operational_state": operational_state,
        "health": health,
        "raw_health": raw_health,
        "paper_execution_available": paper_execution_available,
        "broker_submission_available": bool(state.get("broker_submission_available", False)),
        "execution_authorized": bool(state.get("execution_authorized", False)),
        "connection_state": str(connection.get("status", "disconnected")),
        "automation_mode": str(automation.get("mode", "unknown")),
        "pause_cause": automation.get("pause_cause"),
        "next_action": next_action,
        "blocking_reasons": reasons,
        "counts": {
            "cycles": int(automation.get("cycle_count", 0)),
            "proposals": len(state.get("proposals", [])),
            "executions": len(state.get("executions", [])),
            "reconciliation": len(state.get("reconciliation", [])),
            "audit_events": len(state.get("audit", [])),
        },
    }


def runtime_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        _ensure_month_authorization(state, observed_at)
        _recover_interrupted_cycle(state, observed_at)
        evaluate_runtime_health(state)
        _run_due_cycle(state_path, state, observed_at)
        evaluate_runtime_health(state)
        state["runtime_visibility"] = _runtime_visibility(state, observed_at)
        state["revision"] = int(state["revision"]) + 1
        state["generated_at"] = _timestamp(observed_at)
        state["connection"]["last_refresh_at"] = _timestamp(observed_at)
        _persist(state_path, state)
        return json.loads(json.dumps(state))


def submit_paper_order(request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Create a local paper order; this function has no broker transport."""
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        order = _submit_order(state, request, timestamp=_timestamp(observed_at))
        state["revision"] = int(state["revision"]) + 1
        state["generated_at"] = _timestamp(observed_at)
        _persist(state_path, state)
        return order


def simulate_paper_fill(
    order_id: str,
    market_snapshot: dict[str, Any],
    *,
    quantity: object | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply an injected market snapshot to a local paper order."""
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        order = _fill_order(
            state,
            order_id,
            market_snapshot,
            timestamp=_timestamp(observed_at),
            quantity=quantity,
        )
        state["revision"] = int(state["revision"]) + 1
        state["generated_at"] = _timestamp(observed_at)
        _persist(state_path, state)
        return order


def cancel_paper_order(order_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Cancel an unfilled local paper order and release its reservation."""
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        order = _cancel_order(state, order_id, timestamp=_timestamp(observed_at))
        state["revision"] = int(state["revision"]) + 1
        state["generated_at"] = _timestamp(observed_at)
        _persist(state_path, state)
        return order


def runtime_mission_control_status() -> dict[str, Any]:
    """Return the bounded paper-runtime status required by Mission Control."""
    with _locked_state() as (state_path, state):
        result = mission_control_status(state)
        _persist(state_path, state)
        return result


def control_paper_cycle(action: object, *, now: datetime | None = None) -> dict[str, Any]:
    if action not in CONTROL_ACTIONS:
        raise ValueError("paper automation action must be start, pause, or stop")
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        automation = state["automation"]
        _recover_interrupted_cycle(state, observed_at)

        control_status = str(action)
        control_summary = f"Paper automation {action} recorded"

        if action == "start":
            health = evaluate_runtime_health(state)
            if health != "healthy":
                raise ValueError(f"paper automation cannot start while runtime health is {health}")
            if not _authorization_active(state, observed_at):
                raise ValueError("an active monthly paper authorization is required")
            if automation.get("state") == "running":
                control_status = "start_ignored_already_running"
                control_summary = "Paper automation start ignored because it is already running"
            else:
                automation["state"] = "running"
                automation["next_cycle_at"] = _timestamp(observed_at)
                automation["pause_cause"] = None
                automation["pause_reason"] = None
        elif action == "pause":
            automation["state"] = "paused"
            automation["next_cycle_at"] = None
            automation["pause_cause"] = "manual"
            automation["pause_reason"] = "Paused by owner control"
            _clear_cycle_claim(
                automation,
                last_status="paused",
            )
        else:
            automation.update(
                {
                    "state": "stopped",
                    "next_cycle_at": None,
                    "pause_cause": None,
                    "pause_reason": None,
                }
            )
            _clear_cycle_claim(
                automation,
                last_status="stopped",
            )
        state["revision"] = int(state["revision"]) + 1
        state["generated_at"] = _timestamp(observed_at)
        state["audit"].insert(
            0,
            {
                "id": f"AUD-CONTROL-{state['revision']:06d}",
                "timestamp": _timestamp(observed_at),
                "status": control_status,
                "proposal_id": "—",
                "order_id": "—",
                "evidence_reference": "PAPER-AUTOMATION",
                "summary": control_summary,
                "details": {
                    "broker_submission_attempted": False,
                    "paper_only": True,
                    "authorization_id": state["paper_authorization"].get("authorization_id"),
                },
            },
        )
        evaluate_runtime_health(state)
        state["runtime_visibility"] = _runtime_visibility(state, observed_at)
        _persist(state_path, state)
        return json.loads(json.dumps(state))


def control_paper_authorization(action: object, *, now: datetime | None = None) -> dict[str, Any]:
    if action not in AUTHORIZATION_ACTIONS:
        raise ValueError("paper authorization action must be grant or revoke")
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        authorization = state["paper_authorization"]
        timestamp = _timestamp(observed_at)
        _ensure_month_authorization(state, observed_at)
        if action == "grant":
            if authorization.get("status") == "revoked":
                raise ValueError("paper authorization is revoked for this calendar month")
        else:
            authorization.update({"status": "revoked", "revoked_at": timestamp})
            state["automation"].update(
                {
                    "state": "paused",
                    "next_cycle_at": None,
                    "pause_cause": "safety",
                    "pause_reason": "Paper authorization was revoked",
                }
            )
        state["revision"] = int(state["revision"]) + 1
        state["generated_at"] = timestamp
        state["audit"].insert(
            0,
            {
                "id": f"AUD-AUTH-{state['revision']:06d}",
                "timestamp": timestamp,
                "status": (
                    "authorization_granted" if action == "grant" else "authorization_revoked"
                ),
                "proposal_id": "—",
                "order_id": "—",
                "evidence_reference": str(
                    authorization.get("authorization_id") or "PAPER-AUTHORIZATION"
                ),
                "summary": f"Monthly local paper authorization {action} recorded",
                "details": {
                    "paper_only": True,
                    "scope": authorization["scope"],
                    "expires_at": authorization.get("expires_at"),
                    "broker_submission_attempted": False,
                },
            },
        )
        evaluate_runtime_health(state)
        state["runtime_visibility"] = _runtime_visibility(state, observed_at)
        _persist(state_path, state)
        return json.loads(json.dumps(state))


def reset_paper_runtime(confirmation: object, *, now: datetime | None = None) -> dict[str, Any]:
    if confirmation != "RESET LOCAL PAPER PORTFOLIO":
        raise ValueError("exact paper reset confirmation is required")
    observed_at = now or _now()
    with _locked_state() as (state_path, state):
        reset_id, previous_state_sha256 = _append_reset_evidence(
            state_path.parent, state, observed_at
        )
        reset_state = _empty_reset_state(
            observed_at,
            reset_id=reset_id,
            previous_state_sha256=previous_state_sha256,
        )
        _persist(state_path, reset_state)
        return json.loads(json.dumps(reset_state))
