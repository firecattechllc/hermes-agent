"""Shared SQLite plumbing for Runway's local stores (spec section 23).

Mirrors the idiom already used by ``hermes_state.py``: one file per
subsystem, a ``schema_version`` table, ``CREATE TABLE IF NOT EXISTS`` plus
version-gated migrations. Runway keeps its own ``runway.db`` rather than
adding tables to any existing certified store.

Every store built on this module is local-only (spec section 13: "keep
telemetry local for this phase") and holds no prompt bodies, no source code,
and no credentials — only routing/economic metadata.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from hermes_constants import get_hermes_home


def default_db_path() -> Path:
    return get_hermes_home() / "runway" / "runway.db"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) a Runway SQLite connection.

    ``path=None`` uses :func:`default_db_path`. ``path=":memory:"``-style
    in-memory databases are supported for tests by passing a ``Path`` whose
    ``str()`` is ``":memory:"`` is *not* supported (Path can't represent
    it) — tests should instead pass a ``tmp_path`` fixture file.
    """
    resolved = path if path is not None else default_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection, *, component: str, version: int, statements: Iterable[str]) -> None:
    """Create ``schema_version`` (if absent) and apply ``statements`` exactly
    once per ``component`` at ``version``. Statements should be idempotent
    (``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS``) — this
    function does not attempt incremental ALTER migrations in the foundation
    phase, matching "use migrations/versioning where appropriate" without
    building machinery this phase doesn't need yet.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " component TEXT PRIMARY KEY,"
        " version INTEGER NOT NULL"
        ")"
    )
    row = conn.execute(
        "SELECT version FROM schema_version WHERE component = ?", (component,)
    ).fetchone()
    current = row["version"] if row else 0
    if current > version:
        raise RuntimeError(
            f"runway store {component!r} on-disk schema v{current} is newer than "
            f"code v{version} — refusing to open (fail closed)"
        )
    for statement in statements:
        conn.execute(statement)
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (component, version) VALUES (?, ?)", (component, version)
        )
    elif current < version:
        conn.execute(
            "UPDATE schema_version SET version = ? WHERE component = ?", (version, component)
        )
    conn.commit()
