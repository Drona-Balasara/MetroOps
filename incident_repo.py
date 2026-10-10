"""SQLite-backed incident repository for MetroOps Response.

Single source of truth for incidents and their status history. Uses only the
standard-library sqlite3 module, parameterized SQL, and a short-lived connection
per operation (safe for the single-process local prototype). Status change and
history insert happen in one transaction so they can never disagree.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "metroops.db"

STATUSES = ("Active", "Acknowledged", "In Progress", "Resolved")
_STATUS_SQL = ", ".join(f"'{s}'" for s in STATUSES)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS incidents (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    id                  TEXT UNIQUE,
    created_at          TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ({_STATUS_SQL})),
    station             TEXT NOT NULL,
    category            TEXT NOT NULL,
    description         TEXT NOT NULL,
    passengers_affected INTEGER NOT NULL,
    safety_concern      INTEGER NOT NULL,
    analysis            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_history (
    hid         INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    from_status TEXT,
    to_status   TEXT NOT NULL CHECK (to_status IN ({_STATUS_SQL})),
    at          TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentRepo:
    """Persists incidents and status history in a local SQLite file."""

    def __init__(self, db_path=None):
        self.db_path = str(db_path or os.environ.get("METROOPS_DB") or DEFAULT_DB_PATH)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._open() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _open(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _record(row: sqlite3.Row, history) -> dict:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "station": row["station"],
            "category": row["category"],
            "description": row["description"],
            "passengers_affected": row["passengers_affected"],
            "safety_concern": bool(row["safety_concern"]),
            "analysis": json.loads(row["analysis"]),
            "history": [{"from": h["from_status"], "to": h["to_status"], "at": h["at"]}
                        for h in history],
        }

    def create(self, fields: dict, analysis: dict) -> dict:
        created_at = _now()
        with self._open() as conn, conn:  # one transaction: insert incident + seed history
            cur = conn.execute(
                "INSERT INTO incidents (created_at, status, station, category, "
                "description, passengers_affected, safety_concern, analysis) "
                "VALUES (?, 'Active', ?, ?, ?, ?, ?, ?)",
                (created_at, fields["station"], fields["category"], fields["description"],
                 int(fields["passengers_affected"]), 1 if fields["safety_concern"] else 0,
                 json.dumps(analysis)))
            incident_id = f"INC-{cur.lastrowid:04d}"
            conn.execute("UPDATE incidents SET id = ? WHERE seq = ?", (incident_id, cur.lastrowid))
            conn.execute(
                "INSERT INTO status_history (incident_id, from_status, to_status, at) "
                "VALUES (?, NULL, 'Active', ?)", (incident_id, created_at))
        return self.get(incident_id)

    def get(self, incident_id: str):
        with self._open() as conn:
            row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if row is None:
                return None
            history = conn.execute(
                "SELECT from_status, to_status, at FROM status_history "
                "WHERE incident_id = ? ORDER BY hid", (incident_id,)).fetchall()
        return self._record(row, history)

    def list_all(self) -> list[dict]:
        with self._open() as conn:
            rows = conn.execute("SELECT * FROM incidents ORDER BY seq").fetchall()
            hist_rows = conn.execute(
                "SELECT incident_id, from_status, to_status, at FROM status_history "
                "ORDER BY hid").fetchall()
        by_incident: dict[str, list] = {}
        for h in hist_rows:
            by_incident.setdefault(h["incident_id"], []).append(h)
        return [self._record(r, by_incident.get(r["id"], [])) for r in rows]

    def apply_transition(self, incident_id: str, expected_current: str, new_status: str) -> bool:
        """Atomically move an incident to new_status and log it. Returns False if the
        incident no longer sits at expected_current (concurrent change / gone), leaving
        the row and history untouched."""
        with self._open() as conn, conn:  # UPDATE + history INSERT commit or roll back together
            cur = conn.execute(
                "UPDATE incidents SET status = ? WHERE id = ? AND status = ?",
                (new_status, incident_id, expected_current))
            if cur.rowcount != 1:
                return False
            conn.execute(
                "INSERT INTO status_history (incident_id, from_status, to_status, at) "
                "VALUES (?, ?, ?, ?)", (incident_id, expected_current, new_status, _now()))
            return True
