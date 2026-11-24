"""The host: FastAPI policy gate + web UI.

    uvicorn app:app --reload        ->  http://localhost:8000

This process owns the database and the signing secret. The browser sends what the
traveller says to /api/say; confirmations are checked here and never pass through a model.
"""
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse

from reversa import Reversa
from reversa.catalog import VIBES

app = FastAPI(title="Reversa")
rv = Reversa()


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/state")
def state():
    return rv.state()


@app.post("/api/say")
def say(body: dict = Body(...)):
    """One voice turn. Used by the browser UI."""
    return rv.hear(str(body.get("text", ""))[:500])


# ---- request-only endpoints for the MCP server (the model's side) ------------------------
# None of these can move money on their own: each one that costs anything only creates a
# pending action. There is deliberately no /api/agent/confirm.
@app.post("/api/agent/plan_trip")
def agent_plan(body: dict = Body(...)):
    dest = VIBES.get(str(body.get("destination", "")).lower())
    if not dest:
        return {"say": f"Unknown destination. Options: {sorted(set(VIBES.values()))}"}
    return {"say": rv.start_trip(dest, int(body["nights"]), int(body.get("people", 2)), int(body["budget_rupees"]) * 100)}


@app.post("/api/agent/request_booking")
def agent_book():
    return {"say": rv.request_book()}


@app.post("/api/agent/request_replan")
def agent_replan(body: dict = Body(...)):
    dest = VIBES.get(str(body.get("destination", "")).lower())
    return {"say": rv.request_replan(dest) if dest else "Unknown destination."}


@app.post("/api/agent/request_cancel")
def agent_cancel(body: dict = Body(...)):
    return {"say": rv.request_cancel(body.get("category"))}


# ---- demo controls (the "rest of the world") -------------------------------------------------
@app.post("/api/demo/advance")
def advance(body: dict = Body(...)):
    return rv.advance(float(body.get("hours", 24)))


@app.post("/api/demo/settle")
def settle():
    return rv.settle_refunds()


@app.post("/api/demo/fail_next")
def fail_next(body: dict = Body(...)):
    rv.fail_on.add(body["item_id"])
    return {"ok": True}


@app.post("/api/demo/reset")
def reset():
    rv.ledger.wipe()
    return rv.state()
