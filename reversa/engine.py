"""Reversa engine: hold before commit, priced undo, policy gate, sagas.

The LLM (or the rule-based stand-in in speech.py) only ever produces *requests*.
Anything that moves money becomes a pending action that the traveller must
confirm by speaking its amount back. Only then does the gate mint a signed,
single-use token, and only a valid token lets a saga run.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

from . import speech
from .catalog import CATS, DEST_NAMES, cancel_fee, fee_bps
from .ledger import COMMITTED, FEES, FREE, FUNDING, HELD, REFUND_PENDING, Ledger
from .solver import NoPlan, solve
from .speech import say_rupees as rs

DEPARTS_IN_HOURS = 240     # the demo trip leaves 10 days after the session starts
TOKEN_TTL_HOURS = 1


class GateError(Exception):
    """The policy gate refused."""


class SupplierError(Exception):
    """A (simulated) supplier call failed."""


class Reversa:
    def __init__(self, path: str | None = None, secret: bytes | None = None):
        self.ledger = Ledger(path or os.environ.get("REVERSA_DB", "reversa.db"))
        # Lives only in this process. The MCP server talks to us over HTTP and never sees it.
        self.secret = secret or secrets.token_bytes(32)
        self.fail_on: set[str] = set()   # item ids whose next supplier commit should time out (demo/test)

    # ------------------------------------------------------------------ clock
    def hours_left(self) -> float:
        return DEPARTS_IN_HOURS - self.ledger.get("elapsed_h", 0)

    def advance(self, hours: float) -> dict:
        self.ledger.put("elapsed_h", min(DEPARTS_IN_HOURS, self.ledger.get("elapsed_h", 0) + hours))
        self.ledger.put("pending", None)          # prices moved: any read-back is stale
        return self.state()

    # ------------------------------------------------- projection from the log
    def items(self) -> dict[str, dict]:
        """Current bookings, derived purely by replaying events."""
        items: dict[str, dict] = {}
        for e in self.ledger.events():
            p, t = e["payload"], e["type"]
            if t == "hold":
                items[p["item"]["id"]] = {**p["item"], "status": "held"}
            elif t == "release":
                items.pop(p["id"], None)
            elif t == "commit":
                items[p["id"]]["status"] = "committed"
            elif t == "cancel":
                items.pop(p["id"], None)
            elif t == "change":
                items.pop(p["from"], None)
                items[p["to"]["id"]] = {**p["to"], "status": "committed"}
        return items

    def by_status(self, status: str) -> list[dict]:
        order = {c: i for i, c in enumerate(CATS)}
        return sorted((i for i in self.items().values() if i["status"] == status), key=lambda i: order[i["cat"]])

    def exposure(self, hours_left: float | None = None) -> int:
        """What cancelling everything right now would cost. Holds are free."""
        h = self.hours_left() if hours_left is None else hours_left
        return sum(cancel_fee(i, h) for i in self.by_status("committed"))

    def forecast(self) -> list[dict]:
        """E(t): how exposure steps up as departure approaches."""
        marks = sorted({m for i in self.by_status("committed") for m, _ in i["curve"] if m < self.hours_left()},
                       reverse=True)
        out = [{"hours_left": self.hours_left(), "exposure": self.exposure()}]
        for m in marks:
            h = m - 0.01 if m else 0
            e = self.exposure(h)
            if e != out[-1]["exposure"]:
                out.append({"hours_left": m, "exposure": e})
        return out

    # ---------------------------------------------------------- ledger moves
    def _hold(self, item: dict):
        self.ledger.append("hold", {"item": item}, {FREE: -item["price"], HELD: item["price"]})

    def _release(self, item: dict):
        self.ledger.append("release", {"id": item["id"]}, {HELD: -item["price"], FREE: item["price"]})

    def _supplier_commit(self, item: dict):
        if item["id"] in self.fail_on:
            self.fail_on.discard(item["id"])
            raise SupplierError(f"{item['name']}: supplier timed out")

    def _commit(self, item: dict, key: str):
        self._supplier_commit(item)
        self.ledger.append("commit", {"id": item["id"], "name": item["name"]},
                           {HELD: -item["price"], COMMITTED: item["price"]}, idem=f"{key}:commit:{item['id']}")

    def _cancel(self, item: dict, key: str, why: str = "") -> int:
        fee = cancel_fee(item, self.hours_left())
        self.ledger.append("cancel", {"id": item["id"], "name": item["name"], "fee": fee, "why": why},
                           {COMMITTED: -item["price"], FEES: fee, REFUND_PENDING: item["price"] - fee},
                           idem=f"{key}:cancel:{item['id']}")
        return fee

    def _ladder(self, items: list[dict]) -> list[dict]:
        """Priced commit ladder: cheapest-to-undo first, so a failure half-way costs the least."""
        h = self.hours_left()
        return sorted(items, key=lambda i: (cancel_fee(i, h), i["price"], i["id"]))

    def _commit_ladder(self, items: list[dict], key: str) -> tuple[list[dict], str | None, int]:
        """Returns (committed, error, compensation_fees). On failure, compensates in reverse order."""
        done: list[dict] = []
        for it in self._ladder(items):
            try:
                self._commit(it, key)
                done.append(it)
            except SupplierError as e:
                fees = sum(self._cancel(d, key, why="saga compensation") for d in reversed(done))
                return [], str(e), fees
        return done, None, 0

    # ------------------------------------------------------------ the trip
    def start_trip(self, dest: str, nights: int, people: int, budget: int) -> str:
        if self.by_status("committed"):
            return ("You already have a booked trip, so I won't start over. "
                    "Say 'actually hills' or 'actually beach' to switch, or cancel a booking.")
        self.ledger.wipe()
        self.ledger.put("trip", {"dest": dest, "nights": nights, "people": people, "budget": budget})
        self.ledger.append("open", {"budget": budget}, {FUNDING: -budget, FREE: budget})
        try:
            plan = solve(dest, nights, people, budget)
        except NoPlan as e:
            return f"I heard {DEST_NAMES[dest]}, {nights} nights, {people} people, budget {rs(budget)}. Sorry: {e}."
        for it in plan["buy"]:
            self._hold(it)
        names = ", ".join(i["name"] for i in plan["buy"])
        return (f"I heard: {DEST_NAMES[dest]}, {nights} nights, {people} people, budget {rs(budget)}. "
                f"I'm holding {names}. Total {rs(plan['total'])}, leaving {rs(budget - plan['total'])} free. "
                f"Nothing is charged yet, so your exposure is {rs(0)}. Say 'book it' when you're ready.")

    # ------------------------------------------------------- policy gate
    def _pend(self, kind: str, amount: int, readback: str, data: dict) -> str:
        version = len(self.ledger.events())
        body = {"kind": kind, "amount": amount, "data": data, "version": version}
        body["plan_hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        body["phrase"] = f"confirm {amount // 100}"
        self.ledger.put("pending", body)
        return f"{readback} To go ahead, say: confirm {amount // 100}."

    def _sign(self, p: dict, nonce: str, exp: str) -> str:
        msg = f"{p['kind']}|{p['plan_hash']}|{p['amount']}|{nonce}|{exp}".encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    def confirm(self, spoken: int | None) -> str:
        """Numeric read-back. Called with what the traveller said, straight from the browser."""
        p = self.ledger.get("pending")
        if not p:
            raise GateError("There is nothing waiting for confirmation.")
        if spoken is None or spoken * 100 != p["amount"]:
            heard = "no clear number" if spoken is None else rs(spoken * 100)
            raise GateError(f"I heard {heard}, but the amount is {rs(p['amount'])}. Nothing was changed. "
                            f"Say: {p['phrase']}.")
        nonce, exp = secrets.token_hex(8), f"{self.ledger.get('elapsed_h', 0) + TOKEN_TTL_HOURS:.2f}"
        return f"{nonce}|{exp}|{self._sign(p, nonce, exp)}"

    def execute(self, token: str) -> str:
        p = self.ledger.get("pending")
        try:
            nonce, exp, sig = token.split("|")
            float(exp)
        except (ValueError, AttributeError):
            raise GateError("Malformed token.")
        if not p or not hmac.compare_digest(sig, self._sign(p, nonce, exp)):
            raise GateError("Token is not valid for the pending action.")
        used = self.ledger.get("used_nonces", [])
        if nonce in used:
            raise GateError("Token already used.")
        if self.ledger.get("elapsed_h", 0) > float(exp):
            raise GateError("Token expired.")
        self.ledger.put("used_nonces", used + [nonce])
        self.ledger.put("pending", None)
        if p["version"] != len(self.ledger.events()):
            raise GateError("The bookings changed after the read-back. Please ask again.")
        return getattr(self, f"_run_{p['kind']}")(p)

    # ------------------------------------------------------------ requests
    def request_book(self) -> str:
        held = self.by_status("held")
        if not held:
            return "There is nothing on hold to book."
        total = sum(i["price"] for i in held)
        t = self.ledger.get("trip")
        return self._pend("commit", total,
                          f"Read-back: {DEST_NAMES[t['dest']]}, {t['nights']} nights, {t['people']} people, "
                          f"{len(held)} bookings, total {rs(total)}.", {"ids": [i["id"] for i in held]})

    def _run_commit(self, p: dict) -> str:
        items = [i for i in self.by_status("held") if i["id"] in p["data"]["ids"]]
        done, err, fees = self._commit_ladder(items, p["plan_hash"])
        if err:
            return (f"Booking stopped: {err}. I reversed what had gone through, in reverse order"
                    + (f", at a cost of {rs(fees)}" if fees else " and at no cost") + ". Your holds are still in place.")
        order = ", then ".join(i["name"] for i in done)
        return f"Booked, cheapest-to-undo first: {order}. {self._exposure_line()}"

    def _exposure_line(self) -> str:
        f = self.forecast()
        line = f"If you cancelled everything right now you would lose {rs(f[0]['exposure'])}."
        nxt = next((x for x in f[1:] if x["exposure"] > f[0]["exposure"]), None)
        if nxt:
            line += f" That rises to {rs(nxt['exposure'])} once you are within {nxt['hours_left']:.0f} hours of departure."
        return line

    def request_replan(self, dest: str) -> str:
        t = self.ledger.get("trip")
        if not t:
            return "Tell me the trip first: where, how many nights, and your budget."
        if dest == t["dest"]:
            return f"You are already going to {DEST_NAMES[dest]}."
        committed, held = self.by_status("committed"), self.by_status("held")
        free = self.ledger.balances()[FREE]
        if not committed:                                   # only holds: switching is free, no gate needed
            try:
                plan = solve(dest, t["nights"], t["people"], free + sum(i["price"] for i in held))
            except NoPlan as e:
                return f"Sorry: {e}."
            for i in held:
                self._release(i)
            for i in plan["buy"]:
                self._hold(i)
            self.ledger.put("trip", {**t, "dest": dest})
            return (f"Switched the holds to {DEST_NAMES[dest]}: {', '.join(i['name'] for i in plan['buy'])}. "
                    f"Total {rs(plan['total'])}. Nothing was charged, exposure is still {rs(0)}.")
        try:
            plan = solve(dest, t["nights"], t["people"], free, committed, self.hours_left())
        except NoPlan as e:
            return f"Sorry, I can't switch to {DEST_NAMES[dest]}: {e}."
        parts = [f"move {c['from']['name']} to {c['to']['name']}" + (f" for {rs(c['fee'])}" if c["fee"] else " at no charge")
                 for c in plan["change"]]
        parts += [f"cancel {c['item']['name']} " + (f"for a {rs(c['fee'])} fee" if c["fee"] else "at no charge")
                  for c in plan["cancel"]]
        parts += [f"book {b['name']} for {rs(b['price'])}" for b in plan["buy"]]
        saved = plan["naive_fees"] - plan["fees"]
        text = (f"Switching to {DEST_NAMES[dest]} costs {rs(plan['fees'])} in fees"
                + (f", {rs(saved)} less than cancelling everything" if saved > 0 else "")
                + f". I would {'; '.join(parts)}. Refunds of "
                  f"{rs(sum(c['refund'] for c in plan['cancel']))} would be pending and can't be spent yet.")
        return self._pend("replan", plan["fees"], text, {"plan": plan, "dest": dest})

    def _run_replan(self, p: dict) -> str:
        plan, key = p["data"]["plan"], p["plan_hash"]
        # Order matters. New legs first, old legs cancelled last: because refunds are
        # fenced off anyway, cancelling early buys nothing and risks an orphaned trip.
        for it in plan["buy"]:
            self._hold(it)
        done, err, fees = self._commit_ladder(plan["buy"], key)
        if err:
            for it in plan["buy"]:
                if it["id"] in self.items() and self.items()[it["id"]]["status"] == "held":
                    self._release(it)
            return (f"Switch stopped: {err}. I reversed the new bookings in reverse order"
                    + (f" at a cost of {rs(fees)}" if fees else "") + ". Your original trip is untouched.")
        for c in plan["change"]:
            up, down = max(0, c["diff"]), max(0, -c["diff"])
            self.ledger.append("change", {"from": c["from"]["id"], "to": c["to"], "fee": c["fee"],
                                          "name": f"{c['from']['name']} → {c['to']['name']}"},
                               {FREE: -(c["fee"] + up), FEES: c["fee"], COMMITTED: up - down, REFUND_PENDING: down},
                               idem=f"{key}:change:{c['from']['id']}")
        for c in plan["cancel"]:
            self._cancel(c["item"], key, why="replan")
        t = self.ledger.get("trip")
        self.ledger.put("trip", {**t, "dest": p["data"]["dest"]})
        b = self.ledger.balances()
        return (f"Done. You are going to {DEST_NAMES[p['data']['dest']]}. Fees paid: {rs(plan['fees'])}. "
                f"{rs(b[REFUND_PENDING])} is refund-pending and excluded from your {rs(b[FREE])} of free cash. "
                f"{self._exposure_line()}")

    def request_cancel(self, cat: str | None) -> str:
        active = [i for i in self.items().values() if i["cat"] == cat]
        if not cat or not active:
            return "Which booking should I cancel: the flight, the hotel or the activity?"
        it = active[0]
        if it["status"] == "held":
            self._release(it)
            return f"Released the hold on {it['name']}. It cost nothing."
        fee = cancel_fee(it, self.hours_left())
        return self._pend("cancel", fee,
                          f"Cancelling {it['name']} costs {rs(fee)} now. {rs(it['price'] - fee)} would come back "
                          f"as a pending refund.", {"id": it["id"]})

    def _run_cancel(self, p: dict) -> str:
        it = self.items()[p["data"]["id"]]
        fee = self._cancel(it, p["plan_hash"], why="traveller request")
        b = self.ledger.balances()
        return (f"Cancelled {it['name']}. Fee {rs(fee)}. {rs(it['price'] - fee)} is marked refund-pending; "
                f"total pending is {rs(b[REFUND_PENDING])} and your spendable cash is still {rs(b[FREE])}.")

    def settle_refunds(self) -> dict:
        r = self.ledger.balances()[REFUND_PENDING]
        if r:
            self.ledger.append("settle", {"amount": r}, {REFUND_PENDING: -r, FREE: r})
        return self.state()

    def reconcile(self) -> dict:
        expected = {HELD: sum(i["price"] for i in self.by_status("held")),
                    COMMITTED: sum(i["price"] for i in self.by_status("committed"))}
        return self.ledger.reconcile(expected)

    # ------------------------------------------------------- one voice turn
    def hear(self, text: str) -> dict:
        """One utterance in, one spoken reply out. Confirmations go to the gate, never to a model."""
        it = speech.parse(text)
        try:
            if it["intent"] == "confirm":
                say = self.execute(self.confirm(it["number"]))
            elif it["intent"] == "trip":
                missing = [k for k in ("dest", "nights", "budget") if not it.get(k)]
                say = (f"I need the {' and '.join(missing).replace('dest', 'destination')} to plan that."
                       if missing else self.start_trip(it["dest"], it["nights"], it["people"], it["budget"]))
            elif it["intent"] == "book":
                say = self.request_book()
            elif it["intent"] == "replan":
                say = self.request_replan(it["dest"])
            elif it["intent"] == "cancel":
                say = self.request_cancel(it.get("cat"))
            elif it["intent"] == "exposure":
                say = self._exposure_line()
            elif it["intent"] == "reconcile":
                r = self.reconcile()
                say = (f"Replayed {len(self.ledger.events())} events. Postings sum to {rs(r['sum_paise'])}. "
                       + ("The books balance." if r["ok"] else "The books do NOT balance."))
            else:
                say = "Sorry, I didn't catch that. Try: Goa, 3 nights, 40,000 rupees, two people."
            ok = True
        except GateError as e:
            say, ok = str(e), False
        return {"heard": text, "intent": it["intent"], "say": say, "ok": ok, "state": self.state()}

    def state(self) -> dict:
        h = self.hours_left()
        items = [{**i, "fee_now": cancel_fee(i, h) if i["status"] == "committed" else 0,
                  "fee_pct": fee_bps(i["curve"], h) / 100 if i["status"] == "committed" else 0}
                 for s in ("committed", "held") for i in self.by_status(s)]
        p = self.ledger.get("pending")
        return {"trip": self.ledger.get("trip"), "items": items, "balances": self.ledger.balances(),
                "exposure": self.exposure(), "forecast": self.forecast(), "hours_left": h,
                "pending": {"kind": p["kind"], "amount": p["amount"], "phrase": p["phrase"]} if p else None,
                "events": self.ledger.events()[-40:], "reconcile": self.reconcile()}
