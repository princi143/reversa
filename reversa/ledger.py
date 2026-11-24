"""Zero-sum ledger on an append-only SQLite event log.

Money is always in exactly one of five states. Every event carries postings
that move paise between states and MUST sum to zero, so money can be moved but
never created or lost. reconcile() replays the log from scratch to prove it.
"""
from __future__ import annotations

import json
import sqlite3

FREE, HELD, COMMITTED, REFUND_PENDING, FEES = "free", "held", "committed", "refund_pending", "fees"
STATES = (FREE, HELD, COMMITTED, REFUND_PENDING, FEES)
FUNDING = "funding"  # the outside world: where the budget came from


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, payload TEXT, postings TEXT, idem TEXT UNIQUE);"
            "CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);")

    # ---- small key/value store for non-money state (clock, trip intent, pending action)
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))

    def wipe(self):
        self.db.execute("DELETE FROM events")
        self.db.execute("DELETE FROM kv")
        self.db.execute("DELETE FROM sqlite_sequence")

    # ---- the log ---------------------------------------------------------------
    def append(self, type_: str, payload: dict, postings: dict[str, int], idem: str | None = None) -> bool:
        """Append one event. Returns False (and writes nothing) if this idempotency
        key was already recorded - retrying a saga step can never double-post."""
        postings = {k: v for k, v in postings.items() if v}
        if sum(postings.values()) != 0:
            raise LedgerError(f"{type_}: postings sum to {sum(postings.values())} paise, not 0")
        if any(not isinstance(v, int) for v in postings.values()):
            raise LedgerError("postings must be integer paise")
        bal = self.balances()
        for k, v in postings.items():
            if k != FUNDING and bal.get(k, 0) + v < 0:
                raise LedgerError(f"{type_}: would overdraw '{k}' ({bal.get(k, 0)} {v:+d})")
        try:
            self.db.execute("INSERT INTO events(type,payload,postings,idem) VALUES(?,?,?,?)",
                            (type_, json.dumps(payload), json.dumps(postings), idem))
        except sqlite3.IntegrityError:
            return False
        return True

    def events(self) -> list[dict]:
        return [{"id": r["id"], "type": r["type"], "payload": json.loads(r["payload"]),
                 "postings": json.loads(r["postings"])} for r in self.db.execute("SELECT * FROM events ORDER BY id")]

    def balances(self) -> dict[str, int]:
        bal = {s: 0 for s in STATES}
        for e in self.events():
            for k, v in e["postings"].items():
                bal[k] = bal.get(k, 0) + v
        return {s: bal[s] for s in STATES}

    def reconcile(self, expected: dict[str, int] | None = None) -> dict:
        """Replay every event from zero and check the invariants."""
        bal = {s: 0 for s in STATES}
        bal[FUNDING] = 0
        checks, bad_events = [], []
        for e in self.events():
            if sum(e["postings"].values()) != 0:
                bad_events.append(e["id"])
            for k, v in e["postings"].items():
                bal[k] = bal.get(k, 0) + v
        total = sum(bal.values())
        budget = -bal[FUNDING]
        in_states = sum(bal[s] for s in STATES)
        checks.append({"name": "Every event's postings sum to ₹0", "ok": not bad_events,
                       "detail": f"{len(self.events())} events replayed" + (f", bad: {bad_events}" if bad_events else "")})
        checks.append({"name": "All postings sum to ₹0", "ok": total == 0, "detail": f"sum = {total} paise"})
        checks.append({"name": "Five states add up to the budget", "ok": in_states == budget,
                       "detail": f"{in_states} paise in states, {budget} paise funded"})
        checks.append({"name": "No state is negative", "ok": all(bal[s] >= 0 for s in STATES), "detail": ""})
        if expected is not None:
            ok = all(bal[s] == expected.get(s, bal[s]) for s in (HELD, COMMITTED))
            checks.append({"name": "Ledger matches the bookings", "ok": ok,
                           "detail": f"held {bal[HELD]} vs {expected.get(HELD)}, committed {bal[COMMITTED]} vs {expected.get(COMMITTED)}"})
        return {"ok": all(c["ok"] for c in checks), "sum_paise": total, "balances": {s: bal[s] for s in STATES},
                "budget": budget, "checks": checks}
