"""Budget / fuel system foundation (spec section 10).

Entirely simulated: :class:`BudgetLedger` is a local SQLite ledger, never
connected to a real payment system. A rejected spend attempt writes nothing
— the ledger is only ever mutated by successful, capped spends — so replaying
a rejected request can never silently leak budget.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from runway import storage

_SCHEMA_VERSION = 1
_COMPONENT = "budget_ledger"

_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS budget_spend ("
    " entry_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " timestamp INTEGER NOT NULL,"
    " day_key TEXT NOT NULL,"
    " month_key TEXT NOT NULL,"
    " provider_id TEXT NOT NULL,"
    " model_key TEXT NOT NULL,"
    " task_type TEXT NOT NULL,"
    " amount_micros INTEGER NOT NULL,"
    " is_premium_escalation INTEGER NOT NULL"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_budget_spend_day ON budget_spend(day_key)",
    "CREATE INDEX IF NOT EXISTS idx_budget_spend_month ON budget_spend(month_key)",
    "CREATE INDEX IF NOT EXISTS idx_budget_spend_provider ON budget_spend(provider_id, day_key)",
    "CREATE INDEX IF NOT EXISTS idx_budget_spend_model ON budget_spend(model_key, day_key)",
    # Separate table (not an ALTER on budget_spend) for the optional
    # per-channel cap added for the multi-gateway exchange layer — see
    # runway/providers/gateway/. Additive only; existing rows/queries above
    # are untouched.
    "CREATE TABLE IF NOT EXISTS channel_budget_spend ("
    " entry_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " timestamp INTEGER NOT NULL,"
    " day_key TEXT NOT NULL,"
    " channel_id TEXT NOT NULL,"
    " amount_micros INTEGER NOT NULL"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_channel_budget_spend_channel ON channel_budget_spend(channel_id, day_key)",
)


def _day_key(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


def _month_key(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m")


def _hour_start(timestamp: int) -> int:
    return timestamp - (timestamp % 3600)


class BudgetPolicy(BaseModel):
    """Conservative-by-default: every cap defaults to 0, which means "no
    spend permitted" until an operator explicitly sets it (spec section 20:
    "budget policy must default conservatively").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    per_task_max_micros: int = Field(default=0, ge=0)
    per_hour_max_micros: int = Field(default=0, ge=0)
    per_day_max_micros: int = Field(default=0, ge=0)
    per_month_max_micros: int = Field(default=0, ge=0)
    provider_daily_caps_micros: Dict[str, int] = Field(default_factory=dict)
    model_daily_caps_micros: Dict[str, int] = Field(default_factory=dict)
    #: Optional, keyed by runway.providers.gateway channel_id — a rail-level
    #: cap distinct from the provider-level one above (a provider may expose
    #: several channels; each can have its own ceiling).
    channel_daily_caps_micros: Dict[str, int] = Field(default_factory=dict)
    emergency_reserve_micros: int = Field(default=0, ge=0)
    premium_escalation_ceiling_micros: int = Field(default=0, ge=0)
    prefer_free_quota: bool = True


class BudgetRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BudgetLedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: int
    timestamp: int
    provider_id: str
    model_key: str
    task_type: str
    amount_micros: int = Field(..., ge=0)
    is_premium_escalation: bool


class BudgetHeadroom(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_remaining_micros: int
    hour_remaining_micros: int
    day_remaining_micros: int
    month_remaining_micros: int
    provider_day_remaining_micros: Optional[int]
    model_day_remaining_micros: Optional[int]
    channel_day_remaining_micros: Optional[int] = None

    @property
    def binding_remaining_micros(self) -> int:
        """The tightest of all applicable caps — what a candidate must fit
        under to be affordable right now.
        """
        values = [
            self.task_remaining_micros, self.hour_remaining_micros,
            self.day_remaining_micros, self.month_remaining_micros,
        ]
        if self.provider_day_remaining_micros is not None:
            values.append(self.provider_day_remaining_micros)
        if self.model_day_remaining_micros is not None:
            values.append(self.model_day_remaining_micros)
        if self.channel_day_remaining_micros is not None:
            values.append(self.channel_day_remaining_micros)
        return max(0, min(values))


class BudgetLedger:
    def __init__(self, conn: sqlite3.Connection, policy: BudgetPolicy) -> None:
        self._conn = conn
        self._policy = policy
        storage.ensure_schema(conn, component=_COMPONENT, version=_SCHEMA_VERSION, statements=_SCHEMA_STATEMENTS)

    @classmethod
    def open(cls, policy: BudgetPolicy, *, path: Optional[Path] = None) -> "BudgetLedger":
        return cls(storage.connect(path), policy)

    def _sum(self, where: str, params: tuple) -> int:
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(amount_micros), 0) AS total FROM budget_spend WHERE {where}", params
        ).fetchone()
        return int(row["total"])

    def _channel_sum(self, where: str, params: tuple) -> int:
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(amount_micros), 0) AS total FROM channel_budget_spend WHERE {where}", params
        ).fetchone()
        return int(row["total"])

    def headroom(
        self, *, provider_id: str, model_key: str, timestamp: int, channel_id: Optional[str] = None,
    ) -> BudgetHeadroom:
        policy = self._policy
        spent_hour = self._sum("timestamp >= ?", (_hour_start(timestamp),))
        spent_day = self._sum("day_key = ?", (_day_key(timestamp),))
        month_key = _month_key(timestamp)
        spent_month = self._sum("month_key = ?", (month_key,))

        effective_month_cap = max(0, policy.per_month_max_micros - policy.emergency_reserve_micros)

        provider_remaining = None
        if provider_id in policy.provider_daily_caps_micros:
            spent_provider_day = self._sum(
                "provider_id = ? AND day_key = ?", (provider_id, _day_key(timestamp))
            )
            provider_remaining = max(0, policy.provider_daily_caps_micros[provider_id] - spent_provider_day)

        model_remaining = None
        if model_key in policy.model_daily_caps_micros:
            spent_model_day = self._sum(
                "model_key = ? AND day_key = ?", (model_key, _day_key(timestamp))
            )
            model_remaining = max(0, policy.model_daily_caps_micros[model_key] - spent_model_day)

        channel_remaining = None
        if channel_id is not None and channel_id in policy.channel_daily_caps_micros:
            spent_channel_day = self._channel_sum(
                "channel_id = ? AND day_key = ?", (channel_id, _day_key(timestamp))
            )
            channel_remaining = max(0, policy.channel_daily_caps_micros[channel_id] - spent_channel_day)

        return BudgetHeadroom(
            task_remaining_micros=policy.per_task_max_micros,
            hour_remaining_micros=max(0, policy.per_hour_max_micros - spent_hour),
            day_remaining_micros=max(0, policy.per_day_max_micros - spent_day),
            month_remaining_micros=max(0, effective_month_cap - spent_month),
            provider_day_remaining_micros=provider_remaining,
            model_day_remaining_micros=model_remaining,
            channel_day_remaining_micros=channel_remaining,
        )

    def spend(
        self,
        *,
        amount_micros: int,
        provider_id: str,
        model_key: str,
        task_type: str,
        timestamp: int,
        is_premium_escalation: bool = False,
        channel_id: Optional[str] = None,
    ) -> BudgetLedgerEntry:
        """Atomically validate against every applicable cap and, only on
        success, write exactly one row (two, if ``channel_id`` is given —
        both writes happen together, only after every check passes).
        Raises :class:`BudgetRejected` (ledger untouched) on any cap
        violation.
        """
        if amount_micros < 0:
            raise ValueError("amount_micros must be non-negative")

        room = self.headroom(
            provider_id=provider_id, model_key=model_key, timestamp=timestamp, channel_id=channel_id,
        )

        if amount_micros > self._policy.per_task_max_micros:
            raise BudgetRejected("task_cap_exceeded")
        if amount_micros > room.hour_remaining_micros:
            raise BudgetRejected("hourly_cap_exceeded")
        if amount_micros > room.day_remaining_micros:
            raise BudgetRejected("daily_cap_exceeded")

        month_room = room.month_remaining_micros
        if is_premium_escalation:
            month_room += min(self._policy.emergency_reserve_micros, self._policy.premium_escalation_ceiling_micros)
        if amount_micros > month_room:
            raise BudgetRejected("monthly_cap_exceeded")

        if room.provider_day_remaining_micros is not None and amount_micros > room.provider_day_remaining_micros:
            raise BudgetRejected("provider_cap_exceeded")
        if room.model_day_remaining_micros is not None and amount_micros > room.model_day_remaining_micros:
            raise BudgetRejected("model_cap_exceeded")
        if room.channel_day_remaining_micros is not None and amount_micros > room.channel_day_remaining_micros:
            raise BudgetRejected("channel_cap_exceeded")

        cursor = self._conn.execute(
            "INSERT INTO budget_spend "
            "(timestamp, day_key, month_key, provider_id, model_key, task_type, amount_micros, is_premium_escalation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp, _day_key(timestamp), _month_key(timestamp), provider_id, model_key,
                task_type, amount_micros, int(is_premium_escalation),
            ),
        )
        if channel_id is not None:
            self._conn.execute(
                "INSERT INTO channel_budget_spend (timestamp, day_key, channel_id, amount_micros) "
                "VALUES (?, ?, ?, ?)",
                (timestamp, _day_key(timestamp), channel_id, amount_micros),
            )
        self._conn.commit()
        return BudgetLedgerEntry(
            entry_id=cursor.lastrowid, timestamp=timestamp, provider_id=provider_id,
            model_key=model_key, task_type=task_type, amount_micros=amount_micros,
            is_premium_escalation=is_premium_escalation,
        )

    def spent_month_to_date_micros(self, *, timestamp: int) -> int:
        return self._sum("month_key = ?", (_month_key(timestamp),))

    def spent_today_micros(self, *, timestamp: int) -> int:
        return self._sum("day_key = ?", (_day_key(timestamp),))

    def premium_escalation_count(self, *, timestamp: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM budget_spend WHERE month_key = ? AND is_premium_escalation = 1",
            (_month_key(timestamp),),
        ).fetchone()
        return int(row["n"])
