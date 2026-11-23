"""SignalStore backed by a SQLite file (standard library `sqlite3`).

Same interface as the in-memory `state.SignalStore`. Use it when the daily job
must keep its memory (episodes, cooldowns, dismissals, delivery state) across
restarts. Every write is one transaction: the record and its delivery state are
inserted together, and each delivery attempt or seller response updates the row
in one commit.

The browser demo does not load this module (Pyodide ships sqlite3 as a separate
package), so it is left out of web/py_manifest.json.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Optional

from .state import BaseSignalStore, SignalRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    key               TEXT PRIMARY KEY,
    account_id        TEXT NOT NULL,
    as_of             TEXT NOT NULL,
    logic_version     TEXT NOT NULL,
    strength          TEXT NOT NULL,
    confidence        TEXT NOT NULL,
    crm_account_id    TEXT,
    owner_id          TEXT,
    status            TEXT NOT NULL DEFAULT 'OPEN',
    snoozed_until     TEXT,
    dismiss_reason    TEXT,
    history           TEXT NOT NULL DEFAULT '[]',
    delivery_status   TEXT NOT NULL DEFAULT 'PENDING_DELIVERY',
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    delivery_error    TEXT,
    -- One row per account-day. Logic version is deliberately not part of the
    -- key: a re-run under new logic must not open a second signal.
    UNIQUE (account_id, as_of)
);
CREATE INDEX IF NOT EXISTS signals_account ON signals (account_id);
CREATE INDEX IF NOT EXISTS signals_crm ON signals (crm_account_id);
CREATE INDEX IF NOT EXISTS signals_owner ON signals (owner_id, as_of);
CREATE TABLE IF NOT EXISTS processed_days (as_of TEXT PRIMARY KEY);
"""

_COLS = ("key", "account_id", "as_of", "logic_version", "strength", "confidence", "crm_account_id",
         "owner_id", "status", "snoozed_until", "dismiss_reason", "history", "delivery_status",
         "delivery_attempts", "delivery_error")


def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


class SqliteSignalStore(BaseSignalStore):
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path)
        with self.conn:
            self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # row <-> record ------------------------------------------------------
    @staticmethod
    def _row(rec: SignalRecord) -> tuple:
        return (rec.key, rec.account_id, rec.as_of.isoformat(), rec.logic_version, rec.strength,
                rec.confidence, rec.crm_account_id, rec.owner_id, rec.status,
                rec.snoozed_until.isoformat() if rec.snoozed_until else None, rec.dismiss_reason,
                json.dumps(rec.history), rec.delivery_status, rec.delivery_attempts, rec.delivery_error)

    @staticmethod
    def _rec(row: tuple) -> SignalRecord:
        v = dict(zip(_COLS, row))
        return SignalRecord(
            key=v["key"], account_id=v["account_id"], as_of=_d(v["as_of"]), logic_version=v["logic_version"],
            strength=v["strength"], confidence=v["confidence"], crm_account_id=v["crm_account_id"],
            owner_id=v["owner_id"], status=v["status"], snoozed_until=_d(v["snoozed_until"]),
            dismiss_reason=v["dismiss_reason"], history=[tuple(h) for h in json.loads(v["history"])],
            delivery_status=v["delivery_status"], delivery_attempts=v["delivery_attempts"],
            delivery_error=v["delivery_error"])

    # backend hooks -------------------------------------------------------
    def _get(self, key):
        row = self.conn.execute(f"SELECT {', '.join(_COLS)} FROM signals WHERE key = ?", (key,)).fetchone()
        return self._rec(row) if row else None

    def _insert(self, rec):
        with self.conn:
            cur = self.conn.execute(
                f"INSERT OR IGNORE INTO signals ({', '.join(_COLS)}) VALUES ({', '.join('?' * len(_COLS))})",
                self._row(rec))
        return cur.rowcount == 1

    def _update(self, rec):
        sets = ", ".join(f"{c} = ?" for c in _COLS[1:])
        with self.conn:
            self.conn.execute(f"UPDATE signals SET {sets} WHERE key = ?", self._row(rec)[1:] + (rec.key,))

    def _iter(self, account_id=None, crm_account_id=None):
        q = f"SELECT {', '.join(_COLS)} FROM signals"
        if account_id is None:
            rows = self.conn.execute(q).fetchall()
        else:
            rows = self.conn.execute(q + " WHERE account_id = ? OR (? IS NOT NULL AND crm_account_id = ?)",
                                     (account_id, crm_account_id, crm_account_id)).fetchall()
        return [self._rec(r) for r in rows]

    def latest_processed_day(self):
        row = self.conn.execute("SELECT MAX(as_of) FROM processed_days").fetchone()
        return _d(row[0]) if row else None

    def mark_processed(self, as_of):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO processed_days (as_of) VALUES (?)", (as_of.isoformat(),))
