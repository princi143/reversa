"""Reversa booking service: simulated supplier + safety engine + approvals + ledger.

Two kinds of callers, kept deliberately separate:

* The MCP server (mcp_server.py) exposes ONLY  quote / hold / commit / cancel.
  commit and paid cancel need an approval token, which an MCP client cannot create.
* The human-facing web API calls `approve()` - the only place a token is minted,
  and only after the user states the exact amount.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from . import approval, safety
from .approval import TOKEN_TTL_SECONDS
from .catalog import BY_ID, DEPARTS_IN_HOURS, DEST_NAMES, R, cancel_fee, exposure, options
from .ledger import COMMITTED, FEES, FREE, FUNDING, HELD, REFUND_PENDING, Ledger, LedgerError
from .solver import choose
from .speech import say_rupees as rs

DEFAULT_DB = Path(__file__).resolve().parent.parent / "reversa.db"
WALLET_START = 50_000 * R     # demo wallet funded on reset
ACTIVE = ("HELD", "BLOCKED")


class BookingService:
    def __init__(self, path: str | None = None, secret: bytes | None = None, clock=time.time):
        self.ledger = Ledger(path or os.environ.get("REVERSA_DB") or str(DEFAULT_DB))
        self.secret = secret or approval.load_secret()     # never returned by any method
        self.clock = clock
        self.supplier_price_override: dict[str, int] = {}   # simulate a supplier re-pricing (tests)
        if not self.ledger.events():
            self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self) -> None:
        with self.ledger.lock:
            self.ledger.wipe()
            self.ledger.append("wallet_funded", {"amount": WALLET_START}, {FUNDING: -WALLET_START, FREE: WALLET_START},
                               ts=self.clock())

    def _event(self, type_, payload, postings=None, txn_id=None, idem=None) -> bool:
        return self.ledger.append(type_, payload, postings or {}, txn_id=txn_id, idem=idem, ts=self.clock())

    # ------------------------------------------------------- simulated supplier
    def supplier_quote(self, option_id: str) -> int | None:
        o = BY_ID.get(option_id)
        return None if o is None else self.supplier_price_override.get(option_id, o["price"])

    # ================================================================ MCP tools
    def quote(self, destination: str, budget_paise: int, nights: int = 3) -> dict:
        """quote_booking: open a trip and return priced options with cancellation exposure."""
        dest = destination.lower().strip()
        if dest not in DEST_NAMES:
            return {"ok": False, "error": f"Unknown destination '{destination}'. Try: {', '.join(DEST_NAMES.values())}."}
        if budget_paise <= 0:
            return {"ok": False, "error": "Budget must be positive."}
        trip = {"trip_id": "TRIP-" + secrets.token_hex(3).upper(), "dest": dest, "dest_name": DEST_NAMES[dest],
                "nights": nights, "budget": budget_paise, "created_at": self.clock()}
        opts = []
        for o in options(dest):
            ex = exposure(o)
            opts.append({"option_id": o["id"], "category": o["cat"], "supplier": o["supplier"], "name": o["name"],
                         "price": self.supplier_quote(o["id"]), "within_budget": o["price"] <= budget_paise,
                         "cancellation_fee": ex["fee"], "refundable": ex["refundable"], "fee_pct": ex["fee_pct"],
                         "policy_known": ex["known"]})
        with self.ledger.lock:
            self.ledger.put("trip", trip)
            self._event("quote", {"trip_id": trip["trip_id"], "dest": dest, "budget": budget_paise, "options": len(opts)})
        return {"ok": True, "trip": trip, "options": opts, "departs_in_hours": DEPARTS_IN_HOURS}

    def hold(self, trip_id: str, option_id: str) -> dict:
        """hold_booking: reserve one option at the supplier and earmark the money (free -> held)."""
        with self.ledger.lock:
            trip = self.ledger.get("trip")
            if not trip or trip["trip_id"] != trip_id:
                return {"ok": False, "error": f"Unknown trip {trip_id}. Call quote_booking first."}
            o = BY_ID.get(option_id)
            if not o or o["dest"] != trip["dest"]:
                return {"ok": False, "error": f"Option {option_id} is not part of this trip's quote."}
            same_cat = [t for t in self.ledger.txns() if t["trip_id"] == trip_id and t["category"] == o["cat"]]
            if any(t["status"] == "COMMITTED" for t in same_cat):
                return {"ok": False, "error": f"A {o['cat']} is already booked for this trip. Cancel it first."}
            for t in same_cat:                              # changing your mind about a hold is free
                if t["status"] in ACTIVE:
                    self._release(t, "replaced by a new selection")
            price = self.supplier_quote(option_id)
            now = self.clock()
            txn = {"txn_id": "TXN-" + secrets.token_hex(4).upper(), "booking_id": "BK-" + secrets.token_hex(3).upper(),
                   "trip_id": trip_id, "option_id": option_id, "category": o["cat"], "supplier": o["supplier"],
                   "name": o["name"], "amount": price, "cancellation_fee": cancel_fee(o), "committed_amount": 0,
                   "refund_pending": 0, "status": "HELD", "note": "", "created_at": now, "updated_at": now}
            try:
                self._event("hold", {"name": o["name"], "amount": price}, {FREE: -price, HELD: price},
                            txn_id=txn["txn_id"])
            except LedgerError:
                return {"ok": False, "error": f"Wallet has {rs(self.ledger.balances()[FREE])} available; cannot hold {rs(price)}."}
            self.ledger.insert_txn(txn)
            return {"ok": True, "transaction": txn, "exposure": exposure(o),
                    "safety": self.checks(txn["txn_id"], None, "commit")}

    def commit(self, txn_id: str, token: str) -> dict:
        """commit_booking: run the safety engine; only if every check passes, commit at the supplier."""
        with self.ledger.lock:
            txn = self.ledger.txn(txn_id)
            if not txn:
                return {"ok": False, "error": f"Unknown transaction {txn_id}."}
            checks = self.checks(txn_id, token, "commit")
            if not safety.allowed(checks):
                failed = [c for c in checks if c["status"] != "pass"]
                self._event("commit_rejected", {"reasons": [f"{c['name']}: {c['detail']}" for c in failed]}, txn_id=txn_id)
                return {"ok": False, "error": "Blocked by the safety engine: " + "; ".join(c["detail"].rstrip(".") for c in failed) + ".",
                        "safety": checks, "transaction": txn}
            claims = approval.verify_signature(self.secret, token)
            self.ledger.db.execute("UPDATE approvals SET status='USED' WHERE nonce=? AND status='ACTIVE'", (claims["nonce"],))
            # idempotency key = transaction id: a retried / duplicated commit can never post twice
            if not self._event("commit", {"name": txn["name"], "amount": txn["amount"], "nonce": claims["nonce"]},
                               {HELD: -txn["amount"], COMMITTED: txn["amount"]}, txn_id=txn_id, idem=f"commit:{txn_id}"):
                return {"ok": False, "error": "Duplicate commit ignored by the ledger.", "transaction": txn}
            self.ledger.update_txn(txn_id, status="COMMITTED", committed_amount=txn["amount"],
                                   note="committed with approval " + claims["nonce"], updated_at=self.clock())
            txn = self.ledger.txn(txn_id)
            return {"ok": True, "transaction": txn, "safety": checks,
                    "message": f"Booked {txn['name']} for {rs(txn['amount'])}. Booking ref {txn['booking_id']}."}

    def cancel(self, txn_id: str, token: str | None = None) -> dict:
        """cancel_booking: a hold is released for free; a committed booking needs an approved fee."""
        with self.ledger.lock:
            txn = self.ledger.txn(txn_id)
            if not txn:
                return {"ok": False, "error": f"Unknown transaction {txn_id}."}
            if txn["status"] in ACTIVE:
                self._release(txn, "cancelled before commit")
                return {"ok": True, "transaction": self.ledger.txn(txn_id),
                        "message": f"Released the hold on {txn['name']}. No charge."}
            if not token:
                return {"ok": False, "needs_approval": True, "transaction": txn,
                        "safety": self.checks(txn_id, None, "cancel"),
                        "error": f"Cancelling {txn['name']} costs {rs(txn['cancellation_fee'] or 0)}. "
                                 f"The user must approve that exact fee first."}
            checks = self.checks(txn_id, token, "cancel")
            if not safety.allowed(checks):
                failed = [c for c in checks if c["status"] != "pass"]
                self._event("cancel_rejected", {"reasons": [f"{c['name']}: {c['detail']}" for c in failed]}, txn_id=txn_id)
                return {"ok": False, "error": "Blocked by the safety engine: " + "; ".join(c["detail"].rstrip(".") for c in failed) + ".",
                        "safety": checks, "transaction": txn}
            claims = approval.verify_signature(self.secret, token)
            self.ledger.db.execute("UPDATE approvals SET status='USED' WHERE nonce=? AND status='ACTIVE'", (claims["nonce"],))
            fee, amt = txn["cancellation_fee"], txn["amount"]
            if not self._event("cancel", {"name": txn["name"], "fee": fee, "refund": amt - fee},
                               {COMMITTED: -amt, FEES: fee, REFUND_PENDING: amt - fee}, txn_id=txn_id, idem=f"cancel:{txn_id}"):
                return {"ok": False, "error": "Duplicate cancel ignored by the ledger.", "transaction": txn}
            self.ledger.update_txn(txn_id, status="CANCELLED", committed_amount=0, refund_pending=amt - fee,
                                   note=f"cancelled, fee {rs(fee)}", updated_at=self.clock())
            return {"ok": True, "transaction": self.ledger.txn(txn_id), "safety": checks,
                    "message": f"Cancelled {txn['name']}. Fee {rs(fee)}; {rs(amt - fee)} is refund-pending "
                               f"and NOT counted as available until the refund settles."}

    def _release(self, txn: dict, why: str):
        self._event("release", {"name": txn["name"], "amount": txn["amount"], "why": why},
                    {HELD: -txn["amount"], FREE: txn["amount"]}, txn_id=txn["txn_id"], idem=f"release:{txn['txn_id']}")
        self._revoke(txn["txn_id"])
        self.ledger.update_txn(txn["txn_id"], status="RELEASED", note=why, updated_at=self.clock())

    # ============================================== human-only: approvals
    def approve(self, txn_id: str, confirmed_amount: int | None, action: str = "commit", heard_text: str = "") -> dict:
        """The user states the exact amount. If it matches, mint a signed single-use token.
        If it does not match, STOP: block the transaction and revoke any earlier approval."""
        with self.ledger.lock:
            txn = self.ledger.txn(txn_id)
            if not txn:
                return {"ok": False, "error": f"Unknown transaction {txn_id}."}
            if action not in ("commit", "cancel"):
                return {"ok": False, "error": "action must be 'commit' or 'cancel'."}
            expected = txn["amount"] if action == "commit" else txn["cancellation_fee"]
            if confirmed_amount is None or confirmed_amount != expected:
                heard = "no clear amount" if confirmed_amount is None else rs(confirmed_amount)
                what = "booking amount" if action == "commit" else "cancellation fee"
                msg = (f"I heard {heard}. Your intended {what} was {rs(expected or 0)}. "
                       f"I have stopped the transaction. Please confirm the correct amount.")
                self._revoke(txn_id)
                if action == "commit" and txn["status"] in ACTIVE:
                    self.ledger.update_txn(txn_id, status="BLOCKED", updated_at=self.clock(),
                                           note=f"Heard {heard}, expected {rs(expected)}. Re-confirm required.")
                self._event("approval_refused", {"heard": confirmed_amount, "expected": expected, "action": action,
                                                 "heard_text": heard_text[:200]}, txn_id=txn_id)
                return {"ok": False, "blocked": True, "message": msg, "safety": self.checks(txn_id, None, action),
                        "transaction": self.ledger.txn(txn_id)}
            pre = self.checks(txn_id, None, action)
            hard_fail = [c for c in pre if c["status"] == "fail" and c["id"] in ("budget", "fee_known", "not_executed")]
            if hard_fail:
                self._event("approval_refused", {"reasons": [c["detail"] for c in hard_fail], "action": action}, txn_id=txn_id)
                return {"ok": False, "message": "I can't ask you to approve this: " + "; ".join(c["detail"].rstrip(".") for c in hard_fail) + ".",
                        "safety": pre, "transaction": txn}
            self._revoke(txn_id)                           # a fresh approval supersedes older ones
            now = self.clock()
            claims = approval.new_claims(txn, action, expected, now)
            token = approval.sign(self.secret, claims)
            self.ledger.db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)",
                                   (claims["nonce"], txn_id, action, expected, txn["supplier"], claims["exp"], "ACTIVE", now))
            if txn["status"] == "BLOCKED":
                self.ledger.update_txn(txn_id, status="HELD", note="amount re-confirmed by user", updated_at=now)
            self._event("approval_issued", {"action": action, "amount": expected, "nonce": claims["nonce"],
                                            "expires_in_s": TOKEN_TTL_SECONDS}, txn_id=txn_id)
            return {"ok": True, "token": token, "claims": claims, "safety": self.checks(txn_id, token, action),
                    "transaction": self.ledger.txn(txn_id)}

    def _revoke(self, txn_id: str):
        self.ledger.db.execute("UPDATE approvals SET status='REVOKED' WHERE txn_id=? AND status='ACTIVE'", (txn_id,))

    # ===================================================== safety + views
    def checks(self, txn_id: str, token: str | None, action: str = "commit") -> list[dict]:
        txn = self.ledger.txn(txn_id)
        trip = self.ledger.get("trip")
        trip_budget = trip["budget"] if trip and trip["trip_id"] == txn["trip_id"] else None
        committed = sum(t["committed_amount"] for t in self.ledger.txns()
                        if t["trip_id"] == txn["trip_id"] and t["txn_id"] != txn_id)
        row = None
        if token:
            nonce = approval.describe(token).get("nonce")
            r = self.ledger.db.execute("SELECT * FROM approvals WHERE nonce=?", (nonce,)).fetchone()
            row = dict(r) if r else None
        return safety.evaluate(txn=txn, action=action, trip_budget=trip_budget, committed_in_trip=committed,
                               supplier_price=self.supplier_quote(txn["option_id"]), token=token,
                               approval_row=row, secret=self.secret, now=self.clock())

    def settle_refunds(self) -> dict:
        """Demo control: the bank settles pending refunds; only now do they become available."""
        with self.ledger.lock:
            for t in self.ledger.txns():
                if t["refund_pending"] > 0:
                    self._event("refund_settled", {"name": t["name"], "amount": t["refund_pending"]},
                                {REFUND_PENDING: -t["refund_pending"], FREE: t["refund_pending"]},
                                txn_id=t["txn_id"], idem=f"settle:{t['txn_id']}")
                    self.ledger.update_txn(t["txn_id"], refund_pending=0, note="refund settled", updated_at=self.clock())
        return self.state()

    def compare(self, hours: float = DEPARTS_IN_HOURS) -> list[dict]:
        """Cancellation exposure of every option in the current quote at a given time before departure."""
        trip = self.ledger.get("trip")
        if not trip:
            return []
        return [{"option_id": o["id"], "category": o["cat"], "name": o["name"], "supplier": o["supplier"],
                 "price": self.supplier_quote(o["id"]), "within_budget": o["price"] <= trip["budget"], **exposure(o, hours)}
                for o in options(trip["dest"])]

    def optimise(self, p_pct: int) -> dict:
        trip = self.ledger.get("trip")
        if not trip:
            return {"ok": False, "explanation": "Ask for a trip first."}
        return choose(options(trip["dest"]), trip["budget"], p_pct)

    def active_txn(self, category: str | None = None) -> dict | None:
        trip = self.ledger.get("trip")
        act = [t for t in self.ledger.txns() if t["status"] in ACTIVE and (not trip or t["trip_id"] == trip["trip_id"])
               and (category is None or t["category"] == category)]
        return act[-1] if act else None

    def state(self) -> dict:
        b = self.ledger.balances()
        trip = self.ledger.get("trip")
        txns = self.ledger.txns()
        in_trip = [t for t in txns if trip and t["trip_id"] == trip["trip_id"]]
        spent = sum(t["committed_amount"] for t in in_trip)
        held = sum(t["amount"] for t in in_trip if t["status"] in ACTIVE)
        return {
            "trip": trip,
            "dashboard": {
                "wallet_available": b[FREE], "held": b[HELD], "committed": b[COMMITTED],
                "refund_pending": b[REFUND_PENDING], "fees_paid": b[FEES],
                "trip_budget": trip["budget"] if trip else None,
                "trip_budget_left": (trip["budget"] - spent - held) if trip else None,
                "bookings": sum(1 for t in txns if t["status"] == "COMMITTED"),
            },
            "transactions": txns,
            "approvals": [dict(r) for r in self.ledger.db.execute("SELECT * FROM approvals ORDER BY created_at")],
            "events": self.ledger.events(),
            "reconcile": self.ledger.reconcile(),
            "now": self.clock(),
        }
