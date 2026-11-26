"""Reversa backend tests.   Run:  pytest -q"""
import json
import sqlite3
import sys
from pathlib import Path

import anyio
import pytest

from reversa import BookingService
from reversa.approval import TOKEN_TTL_SECONDS, _b64, describe
from reversa.catalog import BY_ID, cancel_fee, exposure
from reversa.ledger import Ledger, LedgerError
from reversa.solver import choose
from reversa.catalog import options
from reversa.speech import normalize_numbers, parse

R = 100
ROOT = Path(__file__).resolve().parent.parent
FLEXI, STD, SAVER, CHARTER = "fl_goa_flexi", "fl_goa_std", "fl_goa_saver", "fl_goa_charter"
BANYAN, PALM = "ho_goa_banyan", "ho_goa_palm"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock):
    return BookingService(str(tmp_path / "t.db"), secret=b"test-secret", clock=clock)


def trip(svc, budget=20000):
    return svc.quote("goa", budget * R)["trip"]["trip_id"]


def held(svc, option=FLEXI, budget=20000):
    t = trip(svc, budget)
    return svc.hold(t, option)["transaction"]["txn_id"]


def status(checks):
    return {c["id"]: c["status"] for c in checks}


# ---------------------------------------------------------------- successful booking
def test_successful_booking(svc):
    q = svc.quote("goa", 20000 * R)
    assert q["ok"] and {o["option_id"] for o in q["options"]} >= {FLEXI, STD, SAVER, CHARTER}
    h = svc.hold(q["trip"]["trip_id"], FLEXI)
    txn = h["transaction"]["txn_id"]
    assert h["ok"] and svc.ledger.balances()["held"] == 14000 * R
    assert status(h["safety"]) == {"budget": "pass", "fee_known": "pass", "amount": "pending",
                                   "approval": "pending", "not_executed": "pass"}

    a = svc.approve(txn, 14000 * R)
    assert a["ok"] and all(c["status"] == "pass" for c in a["safety"])
    assert set(a["claims"]) == {"txn", "amount", "supplier", "action", "exp", "nonce"}
    assert a["claims"]["supplier"] == "Coastline Airways" and a["claims"]["action"] == "commit"

    c = svc.commit(txn, a["token"])
    assert c["ok"], c
    t = svc.ledger.txn(txn)
    assert t["status"] == "COMMITTED" and t["committed_amount"] == 14000 * R
    b = svc.ledger.balances()
    assert b["committed"] == 14000 * R and b["held"] == 0 and b["free"] == 36000 * R


def test_commit_without_approval_is_blocked(svc):
    txn = held(svc)
    r = svc.commit(txn, "")
    assert not r["ok"] and svc.ledger.txn(txn)["status"] == "HELD"
    assert svc.ledger.balances()["committed"] == 0


# ---------------------------------------------------------------- budget violation
def test_budget_violation(svc):
    txn = held(svc)
    svc.commit(txn, svc.approve(txn, 14000 * R)["token"])
    hotel = svc.hold(svc.ledger.get("trip")["trip_id"], PALM)["transaction"]["txn_id"]   # 14,000 + 8,700 > 20,000
    assert status(svc.checks(hotel, None))["budget"] == "fail"
    a = svc.approve(hotel, 8700 * R)
    assert not a["ok"] and "budget" in a["message"]                     # no token is minted
    assert svc.commit(hotel, "x.y")["ok"] is False
    assert svc.ledger.txn(hotel)["status"] == "HELD" and svc.ledger.balances()["committed"] == 14000 * R


def test_option_over_budget_alone(svc):
    txn = held(svc, FLEXI, budget=12000)
    assert status(svc.checks(txn, None))["budget"] == "fail"
    assert not svc.approve(txn, 14000 * R)["ok"]


# ---------------------------------------------------------------- invalid approval
def test_invalid_approval_tokens(svc):
    txn = held(svc)
    for bad in ("", "garbage", "abc.def", _b64(b'{"amount":1}') + "." + "0" * 64):
        r = svc.commit(txn, bad)
        assert not r["ok"]
    # a genuine token for a different transaction
    other = svc.hold(svc.ledger.get("trip")["trip_id"], BANYAN)["transaction"]["txn_id"]
    tok_other = svc.approve(other, 5400 * R)["token"]
    r = svc.commit(txn, tok_other)
    assert not r["ok"] and status(r["safety"])["approval"] == "fail"
    # a token signed with a different secret (e.g. minted by a model / attacker)
    from reversa.approval import new_claims, sign
    forged = sign(b"not-the-secret", new_claims(svc.ledger.txn(txn), "commit", 14000 * R, svc.clock()))
    r = svc.commit(txn, forged)
    assert not r["ok"] and "signature" in r["error"]
    # a commit token cannot be used to cancel
    assert svc.ledger.txn(txn)["status"] == "HELD"


def test_tampered_token_payload_is_rejected(svc):
    txn = held(svc)
    tok = svc.approve(txn, 14000 * R)["token"]
    claims = describe(tok)
    claims["amount"] = 1 * R                                            # try to lower the approved amount
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    r = svc.commit(txn, body + "." + tok.split(".")[1])
    assert not r["ok"] and "signature" in r["error"]


# ---------------------------------------------------------------- expired approval
def test_expired_approval(svc, clock):
    txn = held(svc)
    tok = svc.approve(txn, 14000 * R)["token"]
    clock.t += TOKEN_TTL_SECONDS + 1
    r = svc.commit(txn, tok)
    assert not r["ok"] and "expired" in r["error"]
    assert svc.ledger.txn(txn)["status"] == "HELD"
    assert svc.commit(txn, svc.approve(txn, 14000 * R)["token"])["ok"]   # fresh approval works


# ---------------------------------------------------------------- modified amount
def test_modified_transaction_amount(svc):
    txn = held(svc)
    tok = svc.approve(txn, 14000 * R)["token"]
    svc.ledger.update_txn(txn, amount=13000 * R)                        # details changed after approval
    r = svc.commit(txn, tok)
    assert not r["ok"] and status(r["safety"])["amount"] == "fail"


def test_supplier_reprice_after_approval(svc):
    txn = held(svc)
    tok = svc.approve(txn, 14000 * R)["token"]
    svc.supplier_price_override[FLEXI] = 15500 * R
    r = svc.commit(txn, tok)
    assert not r["ok"] and "Supplier now quotes" in r["error"]


# ---------------------------------------------------------------- duplicate commit
def test_duplicate_commit(svc):
    txn = held(svc)
    tok = svc.approve(txn, 14000 * R)["token"]
    assert svc.commit(txn, tok)["ok"]
    r = svc.commit(txn, tok)
    assert not r["ok"]
    s = status(r["safety"])
    assert s["approval"] == "fail" and s["not_executed"] == "fail"
    # even a brand-new approval cannot commit it twice
    assert not svc.approve(txn, 14000 * R)["ok"]
    assert svc.ledger.balances()["committed"] == 14000 * R
    assert sum(1 for e in svc.ledger.events() if e["type"] == "commit") == 1
    assert svc.ledger.append("commit", {}, {"held": 0}, idem=f"commit:{txn}") is False   # ledger idempotency


# ---------------------------------------------------------------- misrecognition (feature 7)
def test_misrecognised_amount_blocks_and_needs_fresh_approval(svc):
    txn = held(svc)
    first = svc.approve(txn, 14000 * R)["token"]                        # user had approved ₹14,000
    r = svc.approve(txn, 40000 * R, heard_text="Book the ₹40,000 flight")   # recogniser heard ₹40,000
    assert not r["ok"] and r["blocked"]
    assert r["message"] == ("I heard ₹40,000. Your intended booking amount was ₹14,000. "
                            "I have stopped the transaction. Please confirm the correct amount.")
    assert svc.ledger.txn(txn)["status"] == "BLOCKED"
    assert status(svc.checks(txn, None))["amount"] == "fail"
    old = svc.commit(txn, first)                                        # earlier approval was revoked
    assert not old["ok"] and "revoked" in old["error"]
    fresh = svc.approve(txn, 14000 * R)                                 # user corrects the amount
    assert fresh["ok"] and fresh["token"] != first
    assert svc.commit(txn, fresh["token"])["ok"]
    assert not svc.commit(txn, fresh["token"])["ok"]                    # and it can't be replayed


# ---------------------------------------------------------------- cancellation exposure
def test_cancellation_calculation():
    flexi, std, saver, charter = (BY_ID[i] for i in (FLEXI, STD, SAVER, CHARTER))
    assert cancel_fee(flexi, 240) == 1400 * R and cancel_fee(flexi, 48) == 3500 * R and cancel_fee(flexi, 2) == 14000 * R
    assert cancel_fee(std, 240) == 3125 * R and cancel_fee(std, 100) == 6250 * R
    assert cancel_fee(saver, 240) == 9265 * R                           # 85% always
    assert cancel_fee(charter, 240) is None and exposure(charter)["known"] is False
    e = exposure(flexi, 240)
    assert e["fee"] + e["refundable"] == flexi["price"] and e["fee_pct"] == 10.0
    assert all(isinstance(cancel_fee(BY_ID[i], h), int) for i in (FLEXI, STD, SAVER) for h in (0, 30, 80, 200))


def test_unknown_cancellation_fee_blocks_commit(svc):
    txn = held(svc, CHARTER)
    assert status(svc.checks(txn, None))["fee_known"] == "fail"
    a = svc.approve(txn, 11800 * R)
    assert not a["ok"] and "cancellation policy" in a["message"]


def test_cancel_committed_booking_needs_approved_fee(svc):
    txn = held(svc)
    svc.commit(txn, svc.approve(txn, 14000 * R)["token"])
    r = svc.cancel(txn)
    assert not r["ok"] and r["needs_approval"]
    assert not svc.approve(txn, 14000 * R, action="cancel")["ok"]        # wrong number: the fee is ₹1,400
    tok = svc.approve(txn, 1400 * R, action="cancel")["token"]
    assert svc.cancel(txn, tok)["ok"]
    t = svc.ledger.txn(txn)
    assert t["status"] == "CANCELLED" and t["refund_pending"] == 12600 * R and t["cancellation_fee"] == 1400 * R


def test_hold_release_is_free(svc):
    txn = held(svc)
    assert svc.cancel(txn)["ok"]
    b = svc.ledger.balances()
    assert b["free"] == 50000 * R and b["fees"] == 0 and svc.ledger.txn(txn)["status"] == "RELEASED"


# ---------------------------------------------------------------- ledger
def test_ledger_balance_from_events(svc):
    txn = held(svc)
    svc.commit(txn, svc.approve(txn, 14000 * R)["token"])
    svc.cancel(txn, svc.approve(txn, 1400 * R, action="cancel")["token"])
    b = svc.ledger.balances()
    assert b == {"free": 36000 * R, "held": 0, "committed": 0, "refund_pending": 12600 * R, "fees": 1400 * R}
    # pending refunds are NOT available funds
    assert svc.state()["dashboard"]["wallet_available"] == 36000 * R
    # running balance on the last event equals the replayed balance
    assert svc.ledger.events()[-1]["available_after"] == b["free"]
    rec = svc.ledger.reconcile()
    assert rec["ok"] and rec["sum_paise"] == 0 and sum(b.values()) == 50000 * R
    svc.settle_refunds()
    assert svc.ledger.balances()["free"] == 48600 * R and svc.ledger.reconcile()["ok"]


def test_ledger_persists_across_restart(tmp_path, clock):
    p = str(tmp_path / "p.db")
    s1 = BookingService(p, secret=b"k", clock=clock)
    t = s1.quote("goa", 20000 * R)["trip"]["trip_id"]
    txn = s1.hold(t, FLEXI)["transaction"]["txn_id"]
    s1.commit(txn, s1.approve(txn, 14000 * R)["token"])
    s2 = BookingService(p, secret=b"k", clock=clock)
    assert s2.ledger.txn(txn)["status"] == "COMMITTED" and s2.ledger.balances()["committed"] == 14000 * R
    assert sqlite3.connect(p).execute("SELECT amount FROM transactions").fetchone()[0] == 1400000   # integer paise


def test_ledger_rejects_unbalanced_and_non_integer(tmp_path):
    lg = Ledger(str(tmp_path / "l.db"))
    lg.append("open", {}, {"funding": -100, "free": 100})
    with pytest.raises(LedgerError):
        lg.append("bad", {}, {"free": -50, "held": 49})
    with pytest.raises(LedgerError):
        lg.append("bad", {}, {"free": -0.5, "held": 0.5})
    with pytest.raises(LedgerError):
        lg.append("overdraw", {}, {"free": -200, "held": 200})


# ---------------------------------------------------------------- optimisation
def test_cpsat_choice_shifts_with_cancellation_risk():
    opts = options("goa")
    cheap = choose(opts, 20000 * R, 0)
    assert {c["id"] for c in cheap["chosen"]} == {SAVER, "ho_goa_shack"}
    mid = choose(opts, 20000 * R, 30)
    assert {c["id"] for c in mid["chosen"]} == {STD, "ho_goa_shack"}
    safe = choose(opts, 20000 * R, 100)
    assert {c["id"] for c in safe["chosen"]} == {FLEXI, "ho_goa_shack"}
    for r in (cheap, mid, safe):
        assert r["total_price"] <= 20000 * R and CHARTER not in {c["id"] for c in r["chosen"]}
    assert not choose(opts, 5000 * R, 30)["ok"]


# ---------------------------------------------------------------- speech
@pytest.mark.parametrize("said,amount", [("Book the ₹14,000 flight", 14000), ("book the fourteen thousand flight", 14000),
                                         ("Book the ₹40,000 flight", 40000), ("book the 14k flight", 14000)])
def test_parse_book(said, amount):
    p = parse(said)
    assert p["intent"] == "book" and p["cat"] == "flight" and p["amount"] == amount * R


def test_parse_trip_and_confirm():
    assert parse("Plan a Goa trip under ₹20,000.") == {"intent": "trip", "dest": "goa", "nights": 3, "budget": 20000 * R}
    assert parse("confirm fourteen thousand")["amount"] == 14000 * R
    assert parse("confirm 1 4 0 0 0")["amount"] == 14000 * R
    assert parse("confirm 14000 or 40000")["amount"] is None
    assert normalize_numbers("twenty five thousand eight hundred") == "25800"


# ---------------------------------------------------------------- MCP
def _mcp_json(res):
    return json.loads(res.content[0].text)


def test_mcp_tools_in_memory(svc):
    from mcp.shared.memory import create_connected_server_and_client_session

    from mcp_server import build_server

    async def run():
        async with create_connected_server_and_client_session(build_server(svc)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {"quote_booking", "hold_booking", "commit_booking", "cancel_booking"}
            q = _mcp_json(await client.call_tool("quote_booking", {"destination": "goa", "budget_rupees": 20000}))
            h = _mcp_json(await client.call_tool("hold_booking", {"trip_id": q["trip"]["trip_id"], "option_id": FLEXI}))
            txn = h["transaction"]["txn_id"]
            # the model has no way to approve: committing without a human-minted token fails
            bad = _mcp_json(await client.call_tool("commit_booking", {"transaction_id": txn, "approval_token": "fake"}))
            assert not bad["ok"]
            tok = svc.approve(txn, 14000 * R)["token"]                  # human approval (web API only)
            ok = _mcp_json(await client.call_tool("commit_booking", {"transaction_id": txn, "approval_token": tok}))
            assert ok["ok"] and ok["transaction"]["status"] == "COMMITTED"
            dup = _mcp_json(await client.call_tool("commit_booking", {"transaction_id": txn, "approval_token": tok}))
            assert not dup["ok"]

    anyio.run(run)


def test_mcp_server_over_stdio(tmp_path):
    """Spawn mcp_server.py as a separate process and talk to it over stdio, like Claude Desktop would."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")], cwd=str(ROOT),
                                   env={"REVERSA_DB": str(tmp_path / "s.db"), "REVERSA_SECRET": "stdio-test",
                                        "PATH": "/usr/bin:/bin"})

    async def run():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            assert len((await client.list_tools()).tools) == 4
            q = _mcp_json(await client.call_tool("quote_booking", {"destination": "goa", "budget_rupees": 20000}))
            h = _mcp_json(await client.call_tool("hold_booking", {"trip_id": q["trip"]["trip_id"], "option_id": STD}))
            assert h["ok"] and h["transaction"]["amount"] == 12500 * R

    anyio.run(run)
    db = sqlite3.connect(tmp_path / "s.db")
    assert db.execute("SELECT status FROM transactions").fetchone()[0] == "HELD"   # the subprocess wrote SQLite


# ---------------------------------------------------------------- end-to-end via the web API
def test_web_voice_workflow(tmp_path, clock):
    from fastapi.testclient import TestClient

    from app import create_app
    c = TestClient(create_app(BookingService(str(tmp_path / "w.db"), secret=b"w", clock=clock)))
    say = lambda text, **kw: c.post("/api/say", json={"text": text, **kw}).json()

    r = say("Plan a Goa trip under ₹20,000.")
    assert r["ok"] and len(r["options"]) == 7
    r = say("Book the ₹14,000 flight")
    assert r["ok"] and r["state"]["active"]["transaction"]["amount"] == 14000 * R
    r = say("Book the ₹40,000 flight", simulated_from="Book the ₹14,000 flight")
    assert not r["ok"] and r["reply"].startswith("I heard ₹40,000. Your intended booking amount was ₹14,000.")
    assert r["state"]["dashboard"]["committed"] == 0
    r = say("confirm 14000")
    assert r["ok"] and r["state"]["dashboard"]["committed"] == 14000 * R
    dup = c.post("/api/commit", json={"txn_id": r["txn_id"], "token": r["token"]}).json()
    assert not dup["ok"]
    tools = [m["tool"] for m in dup["state"]["mcp_trace"]]
    assert {"quote_booking", "hold_booking", "commit_booking"} <= set(tools)
    s = c.get("/api/state").json()
    assert s["reconcile"]["ok"] and any(e["type"] == "approval_refused" for e in s["events"])
