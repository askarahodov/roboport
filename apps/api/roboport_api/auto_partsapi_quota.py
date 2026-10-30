"""Persistent single-host quota reservation for manually approved PartsAPI tests.

No provider HTTP request, no secret access, no automatic CI calls. Requires a
durable SQLite path shared by all cooperating processes on ONE host.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .auto_parts_provider import CatalogError, CatalogErrorCode


class PersistentDailyBudget:
    """Fail closed on I/O errors; reserve before calling remote API.

    This does not coordinate across hosts or account for calls outside this
    SQLite database. A human must reconcile portal usage first.
    """

    def __init__(self, path: str | Path, *, cap: int = 50):
        if type(cap) is not int or not 1 <= cap <= 50:
            raise ValueError("cap must be 1..50")
        if not str(path) or str(path) == ":memory:":
            raise ValueError("persistent file required")
        self._path = str(path)
        self._cap = cap

    def reserve(self, *, observed_used: int, now: datetime | None = None) -> int:
        """Reserve one request; observed_used comes from the provider dashboard.

        A reservation is permanently consumed, including when HTTP fails.
        Returns remaining calls after the reservation.
        """
        if type(observed_used) is not int or not 0 <= observed_used <= self._cap:
            raise ValueError("invalid observed usage")
        instant = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("aware datetime required")
        day = instant.astimezone(timezone.utc).date().isoformat()
        try:
            with sqlite3.connect(self._path, timeout=5, isolation_level=None) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS partsapi_quota "
                    "(utc_day TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK (used >= 0))"
                )
                row = conn.execute(
                    "SELECT used FROM partsapi_quota WHERE utc_day = ?", (day,)
                ).fetchone()
                # The dashboard may include requests made outside this process.
                used = max(observed_used, row[0] if row else 0)
                if used >= self._cap:
                    conn.execute("ROLLBACK")
                    raise CatalogError(CatalogErrorCode.RATE_LIMITED)
                conn.execute(
                    "INSERT INTO partsapi_quota(utc_day, used) VALUES (?, ?) "
                    "ON CONFLICT(utc_day) DO UPDATE SET used = excluded.used",
                    (day, used + 1),
                )
                conn.execute("COMMIT")
                return self._cap - used - 1
        except sqlite3.Error:
            raise CatalogError(CatalogErrorCode.UNAVAILABLE) from None
