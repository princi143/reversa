"""Reversa web backend (FastAPI).

    uvicorn app:app --reload        ->  http://localhost:8000   (serves frontend/dist if built)

Every booking operation goes through MCP: this process is an MCP *client* of the
FastMCP server in mcp_server.py, connected over an in-memory MCP session (real
JSON-RPC initialize / tools/list / tools/call messages). Each call is recorded in
the MCP trace shown in the UI.

The one thing that does NOT go through MCP is /api/approve: that is the human
saying the exact amount, and it is the only code path that mints an approval token.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_server import build_server
from reversa import BookingService
from reversa.catalog import DEPARTS_IN_HOURS, options
from reversa.speech import parse
from reversa.speech import say_rupees as rs


class McpBridge:
    """Calls the FastMCP server's tools over a real MCP client session and records a trace."""

    def __init__(self, server):
        self.server = server
        self.trace: deque = deque(maxlen=80)

    async def list_tools(self) -> list[dict]:
        async with create_connected_server_and_client_session(self.server) as client:
            res = await client.list_tools()
        return [{"name": t.name, "description": t.description, "input_schema": t.inputSchema} for t in res.tools]

    async def call(self, tool: str, args: dict) -> dict:
        t0 = time.perf_counter()
        async with create_connected_server_and_client_session(self.server) as client:
            res = await client.call_tool(tool, args)
        text = res.content[0].text if res.content else "{}"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"ok": False, "error": text}
        if res.isError:
            data = {"ok": False, "error": text}
        shown = {k: (v[:18] + "…" if k == "approval_token" and v else v) for k, v in args.items()}
        self.trace.appendleft({"ts": time.time(), "tool": tool, "args": shown, "ok": bool(data.get("ok")),
                               "summary": data.get("message") or data.get("error") or
                                          (f"{len(data.get('options', []))} options" if tool == "quote_booking" else ""),
                               "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return data


def create_app(svc: BookingService | None = None) -> FastAPI:
    svc = svc or BookingService()
    bridge = McpBridge(build_server(svc))
    app = FastAPI(title="Reversa")
    app.state.svc, app.state.bridge = svc, bridge

    def snapshot() -> dict:
        s = svc.state()
        s["mcp_trace"] = list(bridge.trace)
        act = svc.active_txn()
        s["active"] = ({"transaction": act, "safety": svc.checks(act["txn_id"], None, "commit")} if act else None)
        s["pending_cancel"] = svc.ledger.get("pending_cancel")
        return s

    # ------------------------------------------------------------------ reads
    @app.get("/api/state")
    def state():
        return snapshot()

    @app.get("/api/mcp/tools")
    async def tools():
        return await bridge.list_tools()

    @app.get("/api/safety/{txn_id}")
    def safety_preview(txn_id: str, action: str = "commit"):
        if not svc.ledger.txn(txn_id):
            return {"ok": False, "error": "unknown transaction"}
        return {"ok": True, "safety": svc.checks(txn_id, None, action)}

    @app.get("/api/compare")
    def compare(hours: float = DEPARTS_IN_HOURS):
        return {"hours": hours, "options": svc.compare(max(0.0, min(hours, DEPARTS_IN_HOURS)))}

    @app.get("/api/optimise")
    def optimise(p: int = 30):
        return svc.optimise(p)

    # ------------------------------------------------------- MCP-backed actions
    @app.post("/api/quote")
    async def quote(body: dict = Body(...)):
        r = await bridge.call("quote_booking", {"destination": str(body.get("destination", "goa")),
                                                "budget_rupees": int(body.get("budget_rupees", 0))})
        return {**r, "state": snapshot()}

    @app.post("/api/hold")
    async def hold(body: dict = Body(...)):
        trip = svc.ledger.get("trip") or {}
        r = await bridge.call("hold_booking", {"trip_id": trip.get("trip_id", ""), "option_id": str(body["option_id"])})
        return {**r, "state": snapshot()}

    @app.post("/api/commit")
    async def commit(body: dict = Body(...)):
        r = await bridge.call("commit_booking", {"transaction_id": str(body["txn_id"]),
                                                 "approval_token": str(body.get("token") or "")})
        return {**r, "state": snapshot()}

    @app.post("/api/cancel")
    async def cancel(body: dict = Body(...)):
        r = await bridge.call("cancel_booking", {"transaction_id": str(body["txn_id"]),
                                                 "approval_token": str(body.get("token") or "")})
        if r.get("needs_approval"):
            svc.ledger.put("pending_cancel", body["txn_id"])
        elif r.get("ok"):
            svc.ledger.put("pending_cancel", None)
        return {**r, "state": snapshot()}

    # --------------------------------------------- human-only approval gate
    @app.post("/api/approve")
    def approve(body: dict = Body(...)):
        amt = body.get("amount_rupees")
        try:
            paise = None if amt in (None, "") else round(float(str(amt).replace(",", "")) * 100)
        except ValueError:
            paise = None
        r = svc.approve(str(body["txn_id"]), paise, str(body.get("action", "commit")))
        return {**r, "state": snapshot()}

    # ------------------------------------------------------- one voice turn
    @app.post("/api/say")
    async def say(body: dict = Body(...)):
        text = str(body.get("text", ""))[:500]
        intent = parse(text)
        out = {"heard": text, "intent": intent, "simulated_from": body.get("simulated_from")}
        out.update(await _turn(intent, text))
        out["state"] = snapshot()
        return out

    async def _approve_and_commit(txn: dict, amount: int | None, text: str) -> dict:
        if amount is None:
            return {"ok": False, "reply": f"Please say the exact amount to approve {txn['name']}: "
                                          f"'confirm {txn['amount'] // 100}'."}
        a = svc.approve(txn["txn_id"], amount, "commit", heard_text=text)
        if not a["ok"]:
            return {"ok": False, "blocked": a.get("blocked", False), "reply": a["message"], "safety": a.get("safety")}
        c = await bridge.call("commit_booking", {"transaction_id": txn["txn_id"], "approval_token": a["token"]})
        return {"ok": c["ok"], "token": a["token"], "txn_id": txn["txn_id"], "safety": c.get("safety"),
                "reply": (c["message"] + " All five safety checks passed.") if c["ok"] else c["error"]}

    async def _turn(it: dict, text: str) -> dict:
        kind = it["intent"]
        trip = svc.ledger.get("trip")
        if kind == "trip":
            if not it.get("budget"):
                return {"ok": False, "reply": "What's your budget? For example: 'Plan a Goa trip under ₹20,000'."}
            r = await bridge.call("quote_booking", {"destination": it["dest"], "budget_rupees": it["budget"] // 100,
                                                    "nights": it["nights"]})
            if not r["ok"]:
                return {"ok": False, "reply": r["error"]}
            fl = [o for o in r["options"] if o["category"] == "flight"]
            fit = [o for o in fl if o["within_budget"]]
            return {"ok": True, "options": r["options"],
                    "reply": (f"I understood: {r['trip']['dest_name']}, {it['nights']} nights, budget {rs(it['budget'])}. "
                              f"I found {len(fl)} flights ({len(fit)} within budget) and "
                              f"{len(r['options']) - len(fl)} hotels. Pick one, or say e.g. 'Book the ₹14,000 flight'.")}
        if not trip and kind in ("book", "confirm", "cancel", "optimise"):
            return {"ok": False, "reply": "Tell me the trip first, e.g. 'Plan a Goa trip under ₹20,000'."}

        if kind == "book":
            if it.get("ambiguous"):
                return {"ok": False, "reply": "I heard more than one amount, so I've done nothing. Please say one amount."}
            active = svc.active_txn(it.get("cat"))
            match = [o for o in options(trip["dest"]) if o["price"] == it.get("amount")
                     and (it.get("cat") is None or o["cat"] == it["cat"])]
            switching = active and match and match[0]["id"] != active["option_id"]
            if active and not switching:                 # stating the amount of a held booking = approval attempt
                return await _approve_and_commit(active, it.get("amount"), text)
            if it.get("amount") is None:
                return {"ok": False, "reply": "Which one? Say its price, e.g. 'Book the ₹14,000 flight'."}
            if not match:
                return {"ok": False, "reply": f"I heard {rs(it['amount'])}, but no {it.get('cat') or 'option'} costs "
                                              f"that. Nothing was held or charged."}
            h = await bridge.call("hold_booking", {"trip_id": trip["trip_id"], "option_id": match[0]["id"]})
            if not h["ok"]:
                return {"ok": False, "reply": h["error"]}
            t, ex = h["transaction"], h["exposure"]
            fee = (f"Cancellation fee right now is {rs(ex['fee'])}, so {rs(ex['refundable'])} is refundable."
                   if ex["known"] else "The supplier has NOT published a cancellation fee, so I won't be able to commit this.")
            return {"ok": True, "txn_id": t["txn_id"], "safety": h["safety"],
                    "reply": f"Held {t['name']} for {rs(t['amount'])} (transaction {t['txn_id']}). Nothing is charged yet. "
                             f"{fee} To approve, say 'confirm {t['amount'] // 100}'."}

        if kind == "confirm":
            pc = svc.ledger.get("pending_cancel")
            if pc and (svc.ledger.txn(pc) or {}).get("status") == "COMMITTED":
                a = svc.approve(pc, it["amount"], "cancel", heard_text=text)
                if not a["ok"]:
                    return {"ok": False, "blocked": a.get("blocked", False), "reply": a["message"]}
                c = await bridge.call("cancel_booking", {"transaction_id": pc, "approval_token": a["token"]})
                if c["ok"]:
                    svc.ledger.put("pending_cancel", None)
                return {"ok": c["ok"], "token": a["token"], "reply": c.get("message") or c.get("error")}
            active = svc.active_txn()
            if not active:
                return {"ok": False, "reply": "There's nothing waiting for approval."}
            return await _approve_and_commit(active, it["amount"], text)

        if kind == "cancel":
            cands = [t for t in svc.ledger.txns() if t["trip_id"] == trip["trip_id"]
                     and t["status"] in ("HELD", "BLOCKED", "COMMITTED") and (not it.get("cat") or t["category"] == it["cat"])]
            if not cands:
                return {"ok": False, "reply": "There's no active booking to cancel."}
            t = cands[-1]
            r = await bridge.call("cancel_booking", {"transaction_id": t["txn_id"], "approval_token": ""})
            if r.get("needs_approval"):
                svc.ledger.put("pending_cancel", t["txn_id"])
                fee = t["cancellation_fee"] or 0
                return {"ok": True, "reply": f"Cancelling {t['name']} costs {rs(fee)}; {rs(t['amount'] - fee)} would be "
                                             f"refunded (pending, not spendable until it settles). "
                                             f"To go ahead, say 'confirm {fee // 100}'."}
            return {"ok": r["ok"], "reply": r.get("message") or r.get("error")}

        if kind == "ledger":
            rec = svc.ledger.reconcile()
            d = svc.state()["dashboard"]
            return {"ok": True, "reply": f"Replayed {len(svc.ledger.events())} ledger events: postings sum to "
                                         f"{rs(rec['sum_paise'])}, {'books balance' if rec['ok'] else 'books DO NOT balance'}. "
                                         f"Available {rs(d['wallet_available'])}, committed {rs(d['committed'])}, "
                                         f"refund pending {rs(d['refund_pending'])}."}
        if kind == "optimise":
            o = svc.optimise(30)
            return {"ok": o["ok"], "reply": o["explanation"]}
        if kind == "exposure":
            committed = [t for t in svc.ledger.txns() if t["status"] == "COMMITTED"]
            fee = sum(t["cancellation_fee"] or 0 for t in committed)
            return {"ok": True, "reply": f"If you cancelled everything now you would lose {rs(fee)}."}
        return {"ok": False, "reply": "Sorry, I didn't catch that. Try: 'Plan a Goa trip under ₹20,000'."}

    # ------------------------------------------------------------ demo controls
    @app.post("/api/demo/settle")
    def settle():
        svc.settle_refunds()
        return snapshot()

    @app.post("/api/demo/reset")
    def reset():
        svc.reset()
        bridge.trace.clear()
        return snapshot()

    # ------------------------------------------------------------ frontend
    dist = Path(__file__).parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_ui():
            return ("<h3>Reversa API is running.</h3><p>Build the UI: <code>cd frontend && npm install && "
                    "npm run build</code>, or run <code>npm run dev</code> and open http://localhost:5173.</p>")
    return app


app = create_app()
