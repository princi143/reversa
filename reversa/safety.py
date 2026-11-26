"""Financial Safety Engine - deterministic checks that run before any money moves.

No model output is consulted here. Every check reads only the transactions table,
the approvals table, the supplier's current price and the signed token. A booking
is committed only if all five checks return "pass".

Each check is {"id", "name", "status": "pass"|"fail"|"pending", "detail"}.
"""
from __future__ import annotations

from .approval import ApprovalError, verify_signature
from .speech import say_rupees as rs


def _c(id_, name, status, detail):
    return {"id": id_, "name": name, "status": status, "detail": detail}


def evaluate(*, txn: dict, action: str, trip_budget: int | None, committed_in_trip: int,
             supplier_price: int | None, token: str | None, approval_row: dict | None,
             secret: bytes, now: float) -> list[dict]:
    """Run the five checks for `action` ('commit' or 'cancel') on `txn`."""
    checks = []
    expected = txn["amount"] if action == "commit" else txn["cancellation_fee"]

    # 1. Budget --------------------------------------------------------------------------
    if action == "cancel":
        checks.append(_c("budget", "Within the user's budget", "pass", "A cancellation adds no new spend."))
    elif trip_budget is None:
        checks.append(_c("budget", "Within the user's budget", "fail", "No trip budget was set."))
    else:
        total = committed_in_trip + txn["amount"]
        ok = total <= trip_budget
        checks.append(_c("budget", "Within the user's budget", "pass" if ok else "fail",
                         f"{rs(committed_in_trip)} already booked + {rs(txn['amount'])} = {rs(total)} "
                         f"{'≤' if ok else '>'} budget {rs(trip_budget)}"))

    # 2. Cancellation fee known ------------------------------------------------------------
    fee = txn["cancellation_fee"]
    checks.append(_c("fee_known", "Cancellation fee is known",
                     "pass" if fee is not None else "fail",
                     f"Fee if cancelled now: {rs(fee)}" if fee is not None
                     else f"{txn['supplier']} has not published a cancellation policy."))

    # Decode the token once (signature only; binding checked below).
    claims, token_err = None, None
    if token:
        try:
            claims = verify_signature(secret, token)
        except ApprovalError as e:
            token_err = str(e)

    # 3. Exact amount confirmed ---------------------------------------------------------------
    if txn["status"] == "BLOCKED" and not token:
        checks.append(_c("amount", "Exact amount confirmed", "fail", txn.get("note") or "Amount mismatch; re-confirm."))
    elif supplier_price is not None and supplier_price != txn["amount"] and action == "commit":
        checks.append(_c("amount", "Exact amount confirmed", "fail",
                         f"Supplier now quotes {rs(supplier_price)}, transaction says {rs(txn['amount'])}."))
    elif not token:
        checks.append(_c("amount", "Exact amount confirmed", "pending",
                         f"Waiting for the user to confirm {rs(expected) if expected is not None else 'the amount'}."))
    elif claims is None:
        checks.append(_c("amount", "Exact amount confirmed", "fail", "Token unreadable, so no confirmed amount."))
    elif claims.get("amount") != expected:
        checks.append(_c("amount", "Exact amount confirmed", "fail",
                         f"Approved {rs(int(claims.get('amount') or 0))}, but the transaction is {rs(expected)}."))
    else:
        checks.append(_c("amount", "Exact amount confirmed", "pass", f"User confirmed exactly {rs(expected)}."))

    # 4. Explicit, valid approval -------------------------------------------------------------
    name = "User explicitly approved (signed token)"
    if not token:
        checks.append(_c("approval", name, "pending", "No approval token yet."))
    elif token_err:
        checks.append(_c("approval", name, "fail", token_err))
    elif claims.get("txn") != txn["txn_id"]:
        checks.append(_c("approval", name, "fail", f"Token is for {claims.get('txn')}, not {txn['txn_id']}."))
    elif claims.get("action") != action:
        checks.append(_c("approval", name, "fail", f"Token approves '{claims.get('action')}', not '{action}'."))
    elif claims.get("supplier") != txn["supplier"]:
        checks.append(_c("approval", name, "fail", f"Token names supplier {claims.get('supplier')}, not {txn['supplier']}."))
    elif now > claims.get("exp", 0):
        checks.append(_c("approval", name, "fail", "Approval token has expired."))
    elif approval_row is None:
        checks.append(_c("approval", name, "fail", "Token was never issued by this backend."))
    elif approval_row["status"] == "USED":
        checks.append(_c("approval", name, "fail", "Approval token was already used."))
    elif approval_row["status"] == "REVOKED":
        checks.append(_c("approval", name, "fail", "Approval token was revoked (the amount was disputed)."))
    else:
        checks.append(_c("approval", name, "pass", f"Valid for {int(claims['exp'] - now)}s more, single use."))

    # 5. Not already executed ------------------------------------------------------------
    done = (action == "commit" and txn["status"] == "COMMITTED") or (action == "cancel" and txn["status"] == "CANCELLED")
    wrong_state = (action == "commit" and txn["status"] not in ("HELD", "BLOCKED", "COMMITTED")) or \
                  (action == "cancel" and txn["status"] not in ("COMMITTED", "CANCELLED"))
    checks.append(_c("not_executed", "Not already executed",
                     "fail" if done or wrong_state else "pass",
                     f"Transaction is already {txn['status']}; refusing to {action} it again." if done
                     else f"Cannot {action} a {txn['status']} transaction." if wrong_state
                     else f"Status {txn['status']}: {action} has not run yet."))
    return checks


def allowed(checks: list[dict]) -> bool:
    return all(c["status"] == "pass" for c in checks)
