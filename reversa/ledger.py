"""Zero-sum ledger on an append-only SQLite event log.

Money is always in exactly one of five states. Every event carries postings
that move paise between states and MUST sum to zero, so money can be moved but
never created or lost. Balances are never stored: they are recomputed by
replaying the event log, and reconcile() proves the invariants.

Alongside the log there is a `transactions` table (one row per booking attempt)
and an `approvals` table (one row per signed approval token issued).
"""
from __future__ import annotations

import json
import sqlite3
import threading

FREE, HELD, COMMITTED, REFUND_PENDING, FEES = "free", "held", "committed", "refund_pending", "fees"
STATES = (FREE, HELD, COMMITTED, REFUND_PENDING, FEES)
FUNDING = "funding"  # the outside world: where the wallet money came from

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, type TEXT, txn_id TEXT,
    payload TEXT, postings TEXT, idem TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS transactions(
    txn_id TEXT PRIMARY KEY, booking_id TEXT UNIQUE, trip_id TEXT, option_id TEXT,
    category TEXT, supplier TEXT, name TEXT,
    amount INTEGER NOT NULL,            -- booking amount, paise
    cancellation_fee INTEGER,           -- fee if cancelled now (NULL = unknown policy); actual fee once cancelled
    committed_amount INTEGER NOT NULL DEFAULT 0,
    refund_pending INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,               -- HELD | BLOCKED | COMMITTED | RELEASED | CANCELLED
    note TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS approvals(
    nonce TEXT PRIMARY KEY, txn_id TEXT, action TEXT, amount INTEGER, supplier TEXT,
    expires_at REAL, status TEXT,       -- ACTIVE | USED | REVOKED
    created_at REAL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(events)")}
        if cols and "ts" not in cols:      # reversa.db from the earlier prototype: incompatible schema, start fresh
            self.db.executescript("DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS kv;")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    # ---- small key/value store for non-money state (current trip, last quote)
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))

    def wipe(self):
        for t in ("events", "transactions", "approvals", "kv"):
            self.db.execute(f"DELETE FROM {t}")
        self.db.execute("DELETE FROM sqlite_sequence")

    # ---- the log ---------------------------------------------------------------
    def append(self, type_: str, payload: dict, postings: dict[str, int], txn_id: str | None = None,
               idem: str | None = None, ts: float = 0.0) -> bool:
        """Append one event. Returns False (and writes nothing) if this idempotency
        key was already recorded - retrying a step can never double-post."""
        postings = {k: v for k, v in postings.items() if v}
        if any(not isinstance(v, int) or isinstance(v, bool) for v in postings.values()):
            raise LedgerError("postings must be integer paise")
        if sum(postings.values()) != 0:
            raise LedgerError(f"{type_}: postings sum to {sum(postings.values())} paise, not 0")
        bal = self.balances()
        for k, v in postings.items():
            if k != FUNDING and bal.get(k, 0) + v < 0:
                raise LedgerError(f"{type_}: would overdraw '{k}' ({bal.get(k, 0)} {v:+d})")
        try:
            self.db.execute("INSERT INTO events(ts,type,txn_id,payload,postings,idem) VALUES(?,?,?,?,?,?)",
                            (ts, type_, txn_id, json.dumps(payload), json.dumps(postings), idem))
        except sqlite3.IntegrityError:
            return False
        return True

    def events(self) -> list[dict]:
        """Every event, with the running balances AFTER it (computed, never stored)."""
        out, bal = [], {s: 0 for s in STATES}
        for r in self.db.execute("SELECT * FROM events ORDER BY id"):
            postings = json.loads(r["postings"])
            for k, v in postings.items():
                if k in bal:
                    bal[k] += v
            out.append({"id": r["id"], "ts": r["ts"], "type": r["type"], "txn_id": r["txn_id"],
                        "payload": json.loads(r["payload"]), "postings": postings,
                        "available_after": bal[FREE], "balances_after": dict(bal)})
        return out

    def balances(self) -> dict[str, int]:
        bal = {s: 0 for s in STATES}
        for r in self.db.execute("SELECT postings FROM events"):
            for k, v in json.loads(r["postings"]).items():
                if k in bal:
                    bal[k] += v
        return bal

    # ---- transactions ------------------------------------------------------------
    def insert_txn(self, row: dict):
        cols = ",".join(row)
        self.db.execute(f"INSERT INTO transactions({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))

    def update_txn(self, txn_id: str, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE transactions SET {sets} WHERE txn_id=?", (*fields.values(), txn_id))

    def txn(self, txn_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
        return dict(r) if r else None

    def txns(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM transactions ORDER BY created_at, rowid")]

    # ---- reconciliation ------------------------------------------------------------
    def reconcile(self) -> dict:
        """Replay every event from zero and check the invariants against the transactions table."""
        bal = {s: 0 for s in STATES}
        bal[FUNDING] = 0
        bad_events = []
        evs = self.events()
        for e in evs:
            if sum(e["postings"].values()) != 0:
                bad_events.append(e["id"])
            for k, v in e["postings"].items():
                bal[k] = bal.get(k, 0) + v
        total = sum(bal.values())
        funded = -bal[FUNDING]
        in_states = sum(bal[s] for s in STATES)
        txns = self.txns()
        held_txn = sum(t["amount"] for t in txns if t["status"] in ("HELD", "BLOCKED"))
        committed_txn = sum(t["committed_amount"] for t in txns)
        refund_txn = sum(t["refund_pending"] for t in txns)
        checks = [
            {"name": "Every event's postings sum to ₹0", "ok": not bad_events,
             "detail": f"{len(evs)} events replayed" + (f", bad: {bad_events}" if bad_events else "")},
            {"name": "All postings sum to ₹0", "ok": total == 0, "detail": f"sum = {total} paise"},
            {"name": "Five states add up to the funded wallet", "ok": in_states == funded,
             "detail": f"{in_states} paise in states, {funded} paise funded"},
            {"name": "No state is negative", "ok": all(bal[s] >= 0 for s in STATES), "detail": ""},
            {"name": "Held money matches HELD transactions", "ok": bal[HELD] == held_txn,
             "detail": f"ledger {bal[HELD]} vs transactions {held_txn}"},
            {"name": "Committed money matches COMMITTED transactions", "ok": bal[COMMITTED] == committed_txn,
             "detail": f"ledger {bal[COMMITTED]} vs transactions {committed_txn}"},
            {"name": "Refund-pending matches CANCELLED transactions", "ok": bal[REFUND_PENDING] == refund_txn,
             "detail": f"ledger {bal[REFUND_PENDING]} vs transactions {refund_txn}"},
        ]
        return {"ok": all(c["ok"] for c in checks), "sum_paise": total, "funded": funded,
                "balances": {s: bal[s] for s in STATES}, "checks": checks}
